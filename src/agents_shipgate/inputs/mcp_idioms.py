"""The built-in registry of MCP tool-registration idioms (#431).

Most MCP servers never emit their tool surface. ``github/github-mcp-server``
checks in ``__toolsnaps__/*.snap`` and is therefore readable; the official
MongoDB and Grafana servers do not, and ``detect`` reported both as "not an
agent project" — a first-party vendor server publishing ``drop-database`` and
``delete-many`` with no route into the gate at all. The discriminator measured
across three vendor walks was never the language (two of the three are Go); it
was whether the repository happens to *emit* its tool list as a committed
artifact, and only one of the three does.

What every one of them does do is write the tool's name as a **string literal
at a registration site**. The spelling differs per server — ``static toolName =
"aggregate"``, ``mcpgrafana.MustTool("update_incident"``, ``mcp.Tool{Name:
"issue_read"`` — the literal does not. This module reads that literal, and
nothing else: no type inference, no Zod evaluation, no compilation, no
execution. Effects and authority still come from the declaration questionnaire
(#410), which is where they came from for an exported surface too.

**Why a built-in registry and not a configurable pattern.** At ``detect`` time
there is no manifest, so the patterns have to be built in for the activation
case regardless. Post-adoption, a configured regex would hand the definition of
evidence to configuration that ships in the same pull request as the tool it
could hide — the #268 class, where an untrusted artifact gets to say what
counts as evidence about itself. If a configured source ever needs to select
behaviour it selects a *named idiom from this registry* (``go_must_tool``),
never a pattern. Nothing selects one today; the constraint is recorded here so
the first need is met the settled way.

**Why an idiom and not a per-server list.** A per-server list is a support
treadmill and proves nothing transferable. An idiom is a registration *shape*:
the 30-server survey behind this module found five shapes covering every server
whose tools are declared in TypeScript or Go, including all three walked
vendors. Python's ``@mcp.tool`` decorator was the largest single shape in that
survey and shipped one increment later (#484), with its own probe list, because
it is a different **extraction mechanism** rather than a sixth pattern: the
name defaults to the decorated function's, the input schema comes from the
signature, and whether the decorator registers anything at all depends on what
its object is bound to. None of those is a lexical fact, so that idiom is read
with the standard library's parser and the rest of this module's machinery does
not apply to it.

**Honest about what it proved.** Every observation records which idiom matched.
A literal at a registration site is ``medium`` confidence — a committed export
stays ``high`` and remains the better route wherever both exist. A name this
reader cannot resolve to a literal is reported as *unenumerated*, never dropped:
it becomes a typed omission that the exclusion ledger (#403) accounts for and
that holds the whole file's surface at ``partial``.

Reading a *lexed* language is done over a **masked** copy of the source, in
which comments and string bodies have been overwritten (see :func:`mask_source`).
A registration site can therefore never be found inside a comment or inside
another string, and a name is a name only when the masking pass recorded a real
literal at that offset. Where masking cannot complete — an unterminated string
or block comment — the file is reported partial rather than read as though the
rest were code. Python is parsed instead, and a file that does not parse is
reported partial for the same reason.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

#: The version of this registry's published vocabulary — the idiom ids, and the
#: diff tokens the trigger catalog renders from them. Bumped when a consumer
#: pinning an idiom id would have to change; adding an idiom bumps it, because
#: the trigger catalog's token list is derived from this registry and a consumer
#: that mirrors the list has to re-read it.
IDIOM_REGISTRY_VERSION = "2"

SourceLanguage = Literal["typescript", "go", "python"]

#: File suffixes each language's idioms are read from. TypeScript's list covers
#: JavaScript too: the TS SDK is published as JavaScript and a server written
#: against it in ``.mjs`` registers its tools identically.
#:
#: ``.tsx``/``.jsx`` are deliberately absent. JSX puts prose in code position,
#: so an apostrophe in ``<p>don't</p>`` opens a string literal that never
#: closes, and the masking pass would report the file unreadable. That is the
#: fail-closed direction, which is exactly why it is unaffordable here: it
#: would hold a whole repository's surface at ``partial`` over a contraction in
#: a React component. No measured server registers a tool from a JSX module.
LANGUAGE_EXTENSIONS: dict[SourceLanguage, tuple[str, ...]] = {
    "typescript": (".ts", ".mts", ".cts", ".js", ".mjs", ".cjs"),
    "go": (".go",),
    "python": (".py",),
}

#: The languages read with a masking lexer rather than a parser. Python is
#: absent because it has a real parser in the standard library, and #484's
#: measurement is why that matters: a FastMCP tool's name defaults to the
#: *decorated function's*, its schema comes from the *signature*, and whether
#: `@app.tool` registers anything at all depends on what `app` is bound to.
#: None of those are lexical facts, and a masking reader asked for them would
#: be guessing at each one.
LEXED_LANGUAGES: frozenset[SourceLanguage] = frozenset({"typescript", "go"})

#: Declared-dependency tokens that establish an MCP framework for a language.
#: Used by ``detect`` as the provenance gate: an idiom hit in a repository that
#: declares no MCP dependency is a coincidence of spelling until something says
#: otherwise, and #393's lesson is that a proof resting on a spelling is the
#: fail-open shape. Matching is prefix-based for the scoped npm packages and
#: substring-based for Go module paths, both case-insensitively.
TYPESCRIPT_FRAMEWORK_PACKAGES: tuple[str, ...] = (
    "@modelcontextprotocol/",
    "fastmcp",
    "mcp-framework",
    "@mcp-ui/",
    "xmcp",
)
GO_FRAMEWORK_MODULES: tuple[str, ...] = (
    "github.com/modelcontextprotocol/go-sdk",
    "github.com/mark3labs/mcp-go",
    "github.com/metoro-io/mcp-golang",
    "github.com/thinkinaixyz/go-mcp",
    "github.com/ktr0731/go-mcp",
)
#: Distribution names, normalised per PEP 503, that establish an MCP framework
#: for Python. ``mcp`` is the official SDK (whose ``mcp.server.fastmcp``
#: subpackage is FastMCP 1.x) and ``fastmcp`` is the standalone 2.x package;
#: every one of the five servers in the #431 survey declares one of the two.
#:
#: The gate is weaker here than in the other two languages — a *client* also
#: depends on ``mcp`` — and that is on purpose: for Python the load-bearing
#: half of the pairing is not the dependency but the **import binding** the
#: reader itself requires, which is a fact about the same file as the
#: registration. See :func:`python_server_exports`.
PYTHON_FRAMEWORK_PACKAGES: tuple[str, ...] = ("mcp", "fastmcp")

#: ``(module prefix, class)`` pairs whose construction is an MCP server. All
#: three are live and all three were measured, which is why this is a table
#: rather than one module and one class name:
#:
#: * ``fastmcp`` — the standalone 2.x package, and the most common of the
#:   three: 175 modules of ``awslabs/mcp`` import from it, and
#:   ``neo4j-contrib/mcp-neo4j`` from ``fastmcp.server``.
#: * ``mcp.server.fastmcp`` — FastMCP as it shipped inside the official SDK's
#:   v1, which is what ``redis/mcp-redis`` and ``chroma-core/chroma-mcp``
#:   import.
#: * ``mcp.server.mcpserver`` — the same class in the official SDK's v2, where
#:   it was **renamed** to ``MCPServer`` and the old module was replaced by one
#:   that raises ``ModuleNotFoundError``. 41 of ``awslabs/mcp``'s servers had
#:   already moved when this was written, and a reader that knew only the old
#:   name reported every one of their registrations as unenumerable.
PYTHON_SERVER_CONSTRUCTORS: tuple[tuple[str, str], ...] = (
    ("fastmcp", "FastMCP"),
    ("mcp.server.fastmcp", "FastMCP"),
    ("mcp.server.mcpserver", "MCPServer"),
)

#: The cheap gate for the index pass, the same idea as :data:`PREFILTER_TOKEN`
#: and *derived from the table above*, because the two must not drift: a
#: hand-written ``"fastmcp"`` skipped every module built on the SDK's v2 class,
#: which is the largest single shape after the standalone package.
PYTHON_SERVER_PREFILTER_TOKENS: tuple[str, ...] = tuple(
    sorted({symbol.lower() for _module, symbol in PYTHON_SERVER_CONSTRUCTORS})
)

#: The decorator attribute that registers a tool. ``@mcp.resource`` and
#: ``@mcp.prompt`` are the sibling decorators on the same object and register
#: other things; reading either as a tool would put a resource into the action
#: catalog.
PYTHON_TOOL_DECORATOR_ATTR = "tool"

#: Directory names never walked for registration sites. Vendored dependencies
#: and build output contain other people's registrations, and reporting them as
#: this repository's surface is the same over-claim as reading a lockfile.
SKIP_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {
        ".eggs",
        ".git",
        ".hg",
        ".mypy_cache",
        ".next",
        ".nuxt",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".turbo",
        ".venv",
        "__pycache__",
        "bin",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "obj",
        "out",
        "site-packages",
        "target",
        "vendor",
        "venv",
    }
)

#: Path segments whose files declare tools for a test, not for the server. A
#: test's fake tool is not the published surface, and entering one into the
#: catalog invents a capability nobody ships. Go's ``_test.go`` suffix is
#: enforced by the toolchain; the rest are conventions strong enough that every
#: measured server follows them.
TEST_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {
        "__mocks__",
        "__tests__",
        "e2e",
        "fixtures",
        "test",
        "test-fixtures",
        "testdata",
        "tests",
    }
)
_TEST_FILE_SUFFIXES: tuple[str, ...] = (
    "_test.go",
    "_test.py",
    ".test.ts",
    ".test.js",
    ".test.mts",
    ".test.mjs",
    ".spec.ts",
    ".spec.js",
    ".spec.mts",
    ".spec.mjs",
)
#: Python spells a test file with a *prefix*, and it is the dominant spelling:
#: ``pytest`` collects ``test_*.py`` by default, so a suffix-only rule reads
#: ``test_server.py`` as the server. ``conftest.py`` is matched whole because
#: it is neither.
#:
#: Applied to Python only. ``test_helpers.ts`` and ``test_util.go`` are
#: ordinary module names, and an excluded path is never read — so widening
#: this to them would drop a surface with no omission to show for it.
_TEST_FILE_PREFIXES: tuple[str, ...] = ("test_",)
_TEST_FILE_NAMES: frozenset[str] = frozenset({"conftest.py"})

#: Every idiom's pattern requires these four characters, in some case, at the
#: registration site: ``toolName``, ``.tool(``, ``.registerTool(``,
#: ``MustTool(``, ``NewTool(``, ``Tool{``. A file that does not contain them
#: cannot hold a registration, so :func:`scan_source` answers it without
#: masking — which is most of the files in a server's repository.
#:
#: It lives *inside* :func:`scan_source` rather than at the call sites for the
#: reason the rest of this module is built the way it is: a caller's own copy
#: is a second, weaker matcher. The first draft put one in discovery keyed off
#: the trigger catalog's diff tokens, and ``static toolName`` does not appear
#: in ``public static readonly toolName: string = "…"`` — so four MongoDB
#: tools were reported by ``scan`` and never by ``detect``.
PREFILTER_TOKEN = "tool"

#: The shape a tool name has to have to be read as one. A registration site
#: whose literal fails this is reported unenumerated rather than entered into
#: the catalog: ``.tool("Search the web for a query", …)`` is a call this reader
#: matched and did not understand, and saying so is the difference between a
#: measured miss and a silent one.
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: The largest source file this reader opens. Neither hand-written nor
#: generated source reaches it — the largest module in the three vendor
#: repositories is 1.4 MB — so what it bounds is a pathological input, at a
#: masking cost of roughly 0.14 s per megabyte.
#:
#: Deliberately below :data:`agents_shipgate.inputs.common.MAX_INPUT_FILE_BYTES`.
#: Above it the loader would refuse the file first and the omission would be
#: recorded as ``unreadable_file``, naming a decoding problem for a file that
#: was merely large.
#:
#: Exceeding it is recorded, never silently skipped: an unread file is a hole
#: in the enumeration, so it holds the whole source at ``partial``.
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024

#: Every reason token this reader can produce, with the sentence a reviewer
#: acts on. Keyed by the token itself — the adapter writes both onto the same
#: ``SourceSurfaceOmission``, and a detail looked up under a key the reason
#: never uses is a second vocabulary for one event.
#:
#: The first two name a *site*: the reader matched a registration and could not
#: resolve it. The last two name a *file*: past them nothing about the file is
#: known, which is a stronger statement than "this one registration is opaque".
OMISSION_REASONS: dict[str, str] = {
    "name_not_literal": (
        "The tool name at this registration site is not a string literal, so "
        "the name this server registers is decided at runtime and cannot be "
        "read from the source."
    ),
    "implausible_tool_name": (
        "The literal at this registration site is not shaped like a tool name, "
        "so this reader matched a call it does not understand."
    ),
    "unterminated_string": (
        "A string literal is not closed, so past it this reader cannot tell "
        "code from content and a registration there would not be seen."
    ),
    "unterminated_block_comment": (
        "A block comment is not closed, so the rest of this file was read as "
        "comment and a registration there would not be seen."
    ),
    "file_too_large": (
        f"The file is larger than {MAX_SOURCE_FILE_BYTES} bytes, so it was not "
        "read; any tool registered in it is absent from this catalog."
    ),
    "unreadable_file": (
        "The file could not be decoded as UTF-8 source, so it was not read; "
        "any tool registered in it is absent from this catalog."
    ),
    "server_binding_not_proven": (
        "The object this decorator registers on is not one this reader can "
        "follow to an MCP server, so whether it registers a tool — "
        "and under what name — cannot be read from the source."
    ),
    "unparseable_python": (
        "The file is not valid Python, so it was not read; any tool "
        "registered in it is absent from this catalog."
    ),
}


@dataclass(frozen=True)
class RegistrationIdiom:
    """One named, built-in registration shape.

    ``diff_tokens`` are the tokens the *trigger catalog* routes a diff on. The
    catalog's content rule is pinned against them rather than repeating them,
    because a route table kept beside the function that owns the routes drifts
    in the direction nobody checks (#433).

    They are deliberately **narrower** than what this module's patterns match.
    A matched capability rule overrides the workspace stop condition (#403), so
    a router firing on ``.tool(`` or a bare ``Tool{`` would turn "this is not
    an agent project" into "run" for any diff containing that substring. The
    router's job is to stop the *silent* miss, not to be the reader: a
    registration it does not route is still read in full once the repository is
    adopted. Both halves are checked — the catalog against this tuple, and this
    tuple against each idiom's own positive sample.
    """

    id: str
    label: str
    language: SourceLanguage
    reads: str
    diff_tokens: tuple[str, ...]


IDIOMS: tuple[RegistrationIdiom, ...] = (
    RegistrationIdiom(
        id="ts_static_tool_name",
        label="TypeScript tool class with a static name field",
        language="typescript",
        reads=(
            'A class field `static toolName = "…"`, with the sibling '
            "`operationType` and `description` literals from the same class "
            "body when they are present."
        ),
        diff_tokens=("static toolName",),
    ),
    RegistrationIdiom(
        id="ts_sdk_register_tool",
        label="TypeScript SDK call-site registration",
        language="typescript",
        reads=(
            'A `.tool("…", …)` or `.registerTool("…", …)` call whose first '
            "argument is a string literal."
        ),
        diff_tokens=(".registerTool(",),
    ),
    RegistrationIdiom(
        id="go_must_tool",
        label="Go MustTool call-site registration",
        language="go",
        reads='A `MustTool("…", …)` call whose first argument is a string literal.',
        diff_tokens=("MustTool(",),
    ),
    RegistrationIdiom(
        id="go_new_tool",
        label="Go NewTool call-site registration",
        language="go",
        reads='A `NewTool("…", …)` call whose first argument is a string literal.',
        diff_tokens=("NewTool(",),
    ),
    RegistrationIdiom(
        id="go_tool_struct",
        label="Go tool struct literal",
        language="go",
        reads=(
            'A `Tool{…}` composite literal carrying a `Name: "…"` field, the '
            "official Go SDK's declaration shape."
        ),
        diff_tokens=("mcp.Tool{",),
    ),
    RegistrationIdiom(
        id="py_fastmcp_decorator",
        language="python",
        label="Python MCP server tool decorator",
        reads=(
            "A `@mcp.tool` decorator on a function, where `mcp` is bound to a "
            "server construction this reader can follow. The name is "
            "the `name=` literal when one is given and the decorated "
            "function's otherwise, the description is the `description=` "
            "literal or the docstring, and the parameters come from the "
            "signature."
        ),
        diff_tokens=("@mcp.tool",),
    ),
)

IDIOMS_BY_ID: dict[str, RegistrationIdiom] = {idiom.id: idiom for idiom in IDIOMS}

#: Every diff token the registry publishes, de-duplicated and ordered. One
#: list, so the trigger catalog and this reader cannot disagree about which
#: spellings mean "a tool was registered here".
DIFF_TOKENS: tuple[str, ...] = tuple(
    sorted({token for idiom in IDIOMS for token in idiom.diff_tokens})
)


@dataclass(frozen=True)
class RegistrationSite:
    """One registration this reader found, resolved or not.

    ``span`` is the byte range of the construct that matched — the whole call
    including its argument list, or the whole composite literal. Containment of
    one span in another is what lets a wrapper call whose own first argument is
    not a literal (``NewTool(meta, mcp.Tool{Name: "issue_read"})``) stay silent
    instead of reporting an omission for a tool that was, in fact, named.
    """

    idiom: str
    name: str | None
    line: int
    column: int
    span: tuple[int, int]
    description: str | None = None
    operation_type: str | None = None
    unresolved_reason: str | None = None
    #: The decorated function's signature, for an idiom that reads one.
    #: ``None`` means the idiom does not read signatures at all, which is a
    #: different statement from a tool that takes no parameters (``()``).
    parameters: tuple[SignatureParameter, ...] | None = None
    #: The return annotation, rendered back to source. ``None`` when the
    #: function is unannotated or the idiom reads no signature.
    returns: str | None = None
    #: Whether *this site alone* is evidence that the repository is an MCP
    #: server, independently of whether its name could be read.
    #:
    #: For the lexical idioms it is always False: they match a *spelling*, and
    #: `.registerTool(` in a repository that declares no MCP dependency is a
    #: coincidence until the dependency says otherwise. The Python idiom only
    #: emits a site at all once it has followed the decorated object back to a
    #: ``FastMCP(...)`` construction, and a client does not construct a server
    #: — so the site carries its own provenance, and discovery can offer the
    #: route for a server whose every tool name is built at runtime.
    #: ``neo4j-contrib/mcp-neo4j`` is exactly that server: 40 registrations,
    #: every one of them ``name=namespace_prefix + "…"``.
    proves_server: bool = False


@dataclass(frozen=True)
class SignatureParameter:
    """One parameter of a decorated function, as written.

    ``annotation`` is the annotation rendered back to source, never a resolved
    type: resolving one means following imports into libraries this reader does
    not read. The caller maps it to a JSON Schema type.
    """

    name: str
    annotation: str | None = None
    required: bool = True


@dataclass(frozen=True)
class SourceScanResult:
    """What one file yielded.

    ``anomalies`` are failures that hold the whole file — a masking failure for
    a lexed language, a syntax error for Python. They are separate from an
    unresolved site because they are a fact about the *file*, not about one
    registration: past the anomaly this reader cannot tell code from content,
    so a site it did not find there proves nothing.

    ``server_modules`` are the other modules this file's proofs *depended on* —
    the module ``from src.common.server import mcp`` resolved to. They are
    reported because a route that covers the registrations but not the module
    that constructs the server is a route on which ``scan`` proves less than
    ``detect`` did, which is the detect/scan disagreement this input's shared
    path predicate exists to prevent.
    """

    sites: tuple[RegistrationSite, ...] = ()
    anomalies: tuple[str, ...] = ()
    server_modules: tuple[str, ...] = ()


def language_for_path(path: Path | PurePosixPath | str) -> SourceLanguage | None:
    """The language whose idioms apply to ``path``, or ``None``."""

    suffix = PurePosixPath(str(path)).suffix.lower()
    for language, extensions in LANGUAGE_EXTENSIONS.items():
        if suffix in extensions:
            return language
    return None


def is_scannable_path(relative_path: Path | PurePosixPath | str) -> bool:
    """Whether a workspace-relative path is read for registration sites.

    One predicate, used by both the adapter's walk and discovery's probe: two
    copies would disagree about which files are the surface, and the pair that
    disagrees is the pair where ``detect`` promises tools ``scan`` then refuses
    to enumerate.
    """

    path = PurePosixPath(str(relative_path).replace("\\", "/"))
    if language_for_path(path) is None:
        return False
    parts = path.parts
    if any(part in SKIP_DIRECTORY_NAMES for part in parts):
        return False
    if any(part.lower() in TEST_DIRECTORY_NAMES for part in parts[:-1]):
        return False
    name = path.name.lower()
    # Scoped to Python, because the *reason* is: pytest collects `test_*.py`,
    # so a suffix-only rule reads `test_server.py` as the server. `test_util.go`
    # and `test_helpers.ts` are ordinary module names, and excluding one costs
    # a surface with no omission to show for it — an excluded path is never
    # read, so nothing records that it was skipped.
    if language_for_path(path) == "python" and (
        name in _TEST_FILE_NAMES or name.startswith(_TEST_FILE_PREFIXES)
    ):
        return False
    return not name.endswith(_TEST_FILE_SUFFIXES)


def idioms_for_language(language: SourceLanguage) -> tuple[RegistrationIdiom, ...]:
    return tuple(idiom for idiom in IDIOMS if idiom.language == language)


# --- Masking -----------------------------------------------------------------

#: Fill characters. Comments become spaces so a token cannot span the hole they
#: leave; string literals become NULs so a literal's *position* stays findable
#: (``literals`` is keyed by the opening quote's offset) while its content can
#: never be matched as code.
_COMMENT_FILL = " "
_STRING_FILL = "\x00"

# Characters after which a `/` opens a regular expression rather than dividing.
# The standard lexical heuristic; getting it wrong in the other direction is
# what matters, because a regex body read as code can contain `"` and desync
# every string boundary after it.
_REGEX_PRECEDING_CHARS = frozenset("(,=:[!&|?{};+-*%~^<>")
#: Keywords whose parenthesised condition can be followed directly by a regex
#: that begins the statement's body. `)` alone cannot decide: `foo(a) / 2`
#: divides and `if (a) /re/.test(b)` does not.
_REGEX_PRECEDING_STATEMENTS = frozenset(
    {"if", "for", "while", "switch", "catch", "with"}
)
_REGEX_PRECEDING_WORDS = frozenset(
    {
        "return",
        "typeof",
        "instanceof",
        "in",
        "of",
        "new",
        "delete",
        "void",
        "throw",
        "case",
        "do",
        "else",
        "yield",
        "await",
    }
)

#: The only characters that can begin a comment or a string in either language.
#: The masking loop jumps between them instead of visiting every character:
#: ordinary code is the overwhelming majority of any source file, and stepping
#: through it in Python is what made the pass cost a second on a large module.
_INTERESTING = re.compile(r"""[/'"`]""")

#: Escapes both languages spell the same way and mean the same thing.
_SHARED_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
}
#: JavaScript adds a backtick and ``\0`` for NUL. Line continuations are not
#: here: a continuation is a backslash followed by a *line terminator
#: sequence*, and CRLF is one such sequence rather than a ``\r`` escape
#: followed by a break, so it needs a rule that can consume two characters.
_TYPESCRIPT_ESCAPES = {**_SHARED_ESCAPES, "`": "`"}

#: The line terminators a backslash can continue a line across. ``\r`` is here
#: because a CRLF checkout spells the same continuation with two characters,
#: and JavaScript reads both files identically — so a reader that lost the
#: registration on one of them would answer "not an agent project" for a
#: line-ending translation (#485 review).
_TYPESCRIPT_LINE_TERMINATORS = frozenset("\n\r")
#: Go adds the bell and has no line continuation and no bare ``\0``.
_GO_ESCAPES = {**_SHARED_ESCAPES, "a": "\a"}

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_OCTAL_DIGITS = frozenset("01234567")


@dataclass(frozen=True)
class MaskedSource:
    """``text`` with comments and string bodies overwritten.

    ``literals`` maps the offset of a string literal's opening quote to its
    decoded value (``None`` when the literal is not a constant — a template
    literal carrying a ``${…}`` substitution) and the offset just past its
    closing quote. Callers ask "is there a literal here?" through
    :meth:`literal_at`, which is the only way a name is ever read: matching a
    quote in the raw text would accept one inside a comment, and matching one
    in the masked text is impossible by construction.

    The end offset is recorded rather than recovered by scanning the fill
    characters, because masking preserves newlines: the fill run of a
    multi-line template literal stops at its first line break, and the caller
    that asks what follows the literal would be looking inside it.
    """

    text: str
    masked: str
    literals: dict[int, tuple[str | None, int]]
    anomalies: tuple[str, ...]

    def skip_space(self, index: int) -> int:
        """The next index at or after ``index`` that is not whitespace."""

        length = len(self.masked)
        while index < length and self.masked[index].isspace():
            index += 1
        return index

    def literal_at(self, index: int) -> tuple[bool, str | None, int]:
        """Resolve a string literal starting at ``index`` (whitespace skipped).

        Returns ``(found, value, end)``. ``found`` is False when no literal
        starts there at all; ``value`` is ``None`` for a literal whose content
        is not constant. ``end`` is the index just past the literal, or the
        whitespace-skipped index when nothing was found.
        """

        start = self.skip_space(index)
        record = self.literals.get(start)
        if record is None:
            return False, None, start
        value, end = record
        return True, value, end

    def line_column(self, index: int) -> tuple[int, int]:
        """1-based line and column of ``index``."""

        prefix = self.text[:index]
        line = prefix.count("\n") + 1
        column = index - (prefix.rfind("\n") + 1) + 1
        return line, column


def mask_source(text: str, language: SourceLanguage) -> MaskedSource:
    """Overwrite comments and string bodies, recording every string literal."""

    if language not in LEXED_LANGUAGES:
        # Refused rather than defaulted. The dispatch below has no third
        # branch, so a language added to the registry without a masker would
        # otherwise be read with JavaScript's grammar and answer confidently.
        raise ValueError(f"{language!r} is not read with the masking lexer")
    if language == "go":
        return _mask_go(text)
    return _mask_typescript(text)


def decode_literal(body: str, language: SourceLanguage) -> str | None:
    """The literal's value, or ``None`` when it cannot be decoded exactly.

    Escape grammars are per language, and one decoder shared between them is a
    silent mistranslation rather than a parse error. Go writes an octal escape
    as three digits — ``mcp.MustTool("delete\\137all", …)`` registers
    ``delete_all`` — and a JavaScript-shaped decoder read the ``1`` as an
    unknown escape and produced ``delete137all``: the real action absent from
    the catalog, and an action id nobody serves in its place.

    So each language gets its own grammar, and **anything either grammar does
    not define is refused**. A refusal returns ``None``, which reaches
    :func:`_resolve_name` as ``name_not_literal`` — the tool becomes a recorded
    omission instead of a guessed name. Guessing is the one outcome a reader of
    a *name* cannot afford.
    """

    if "\\" not in body:
        return body
    if language == "go":
        return _decode_go(body)
    return _decode_typescript(body)


def _hex_value(body: str, start: int, width: int) -> int | None:
    digits = body[start : start + width]
    if len(digits) != width or any(char not in _HEX_DIGITS for char in digits):
        return None
    return int(digits, 16)


def _decode_typescript(body: str) -> str | None:
    out: list[str] = []
    index = 0
    length = len(body)
    while index < length:
        char = body[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= length:
            return None
        marker = body[index + 1]
        if marker in _TYPESCRIPT_LINE_TERMINATORS:
            # A LineContinuation contributes nothing to the value. CRLF is one
            # terminator sequence: reading it as `\r` plus a stray line break
            # both mangles the value and, in the scanner, ends the string.
            index += 3 if marker == "\r" and body[index + 2 : index + 3] == "\n" else 2
            continue
        if marker in _TYPESCRIPT_ESCAPES:
            out.append(_TYPESCRIPT_ESCAPES[marker])
            index += 2
            continue
        if marker == "0" and (index + 2 >= length or body[index + 2] not in "0123456789"):
            out.append("\0")
            index += 2
            continue
        if marker == "x":
            value = _hex_value(body, index + 2, 2)
            if value is None:
                return None
            out.append(chr(value))
            index += 4
            continue
        if marker == "u":
            if index + 2 < length and body[index + 2] == "{":
                close = body.find("}", index + 3)
                digits = body[index + 3 : close] if close != -1 else ""
                if not digits or any(char not in _HEX_DIGITS for char in digits):
                    return None
                point = int(digits, 16)
                if point > 0x10FFFF:
                    return None
                out.append(chr(point))
                index = close + 1
                continue
            value = _hex_value(body, index + 2, 4)
            if value is None:
                return None
            out.append(chr(value))
            index += 6
            continue
        if marker.isdigit():
            # Legacy octal (`\1`-`\7`) is a syntax error under `use strict`
            # and in a template literal, and octal *elsewhere*; `\8`/`\9` are
            # their own special case. Which one a file means depends on a mode
            # this reader does not track, so it refuses rather than pick.
            return None
        out.append(marker)
        index += 2
    return "".join(out)


def _decode_go(body: str) -> str | None:
    out: list[str] = []
    index = 0
    length = len(body)
    while index < length:
        char = body[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= length:
            return None
        marker = body[index + 1]
        if marker in _GO_ESCAPES:
            out.append(_GO_ESCAPES[marker])
            index += 2
            continue
        if marker in _OCTAL_DIGITS:
            digits = body[index + 1 : index + 4]
            if len(digits) != 3 or any(char not in _OCTAL_DIGITS for char in digits):
                return None
            value = int(digits, 8)
            if value > 255:
                return None
            out.append(chr(value))
            index += 4
            continue
        widths = {"x": 2, "u": 4, "U": 8}
        if marker in widths:
            value = _hex_value(body, index + 2, widths[marker])
            if value is None or value > 0x10FFFF:
                return None
            out.append(chr(value))
            index += 2 + widths[marker]
            continue
        # Every other escape is a Go compile error, so the file this reader is
        # looking at is not the file that built the server.
        return None
    return "".join(out)


class _Masker:
    """Shared bookkeeping for the two language maskers."""

    def __init__(self, text: str, language: SourceLanguage) -> None:
        self.text = text
        self.language = language
        self.out: list[str] = list(text)
        self.literals: dict[int, tuple[str | None, int]] = {}
        self.anomalies: list[str] = []

    def blank(self, start: int, end: int, fill: str) -> None:
        end = min(end, len(self.out))
        if end <= start:
            return
        segment = self.text[start:end]
        # Newlines survive so line numbers stay the file's own. Slice
        # assignment rather than a per-character loop: a masking pass that
        # walked a 1.4 MB generated module one character at a time cost more
        # than a second on a file that registers nothing.
        if "\n" in segment:
            self.out[start:end] = [
                "\n" if char == "\n" else fill for char in segment
            ]
        else:
            self.out[start:end] = fill * (end - start)

    def record(self, start: int, end: int, value: str | None) -> None:
        self.blank(start, end, _STRING_FILL)
        self.literals[start] = (value, end)

    def result(self) -> MaskedSource:
        return MaskedSource(
            text=self.text,
            masked="".join(self.out),
            literals=self.literals,
            anomalies=tuple(self.anomalies),
        )


def _previous_significant(masked: list[str], index: int) -> tuple[str, int]:
    """The last non-whitespace character at or before ``index``, and where."""

    while index >= 0 and masked[index].isspace():
        index -= 1
    return (masked[index], index) if index >= 0 else ("", -1)


def _preceding_word(masked: list[str], index: int) -> str:
    """The identifier ending at or before ``index``, read from the mask.

    The mask, not the raw text: comments have been overwritten with spaces
    there, so `if /*why*/ (ok) /re/` still finds `if`. Reading the raw text
    found `/` — the tail of the comment — decided the slash was division, and
    scanned the regex body as code, which reported a tool invented out of a
    pattern. That is the one outcome masking exists to make impossible.
    """

    while index >= 0 and masked[index].isspace():
        index -= 1
    end = index + 1
    while index >= 0 and (masked[index].isalnum() or masked[index] in "_$"):
        index -= 1
    return "".join(masked[index + 1 : end])


def _mask_typescript(text: str) -> MaskedSource:
    masker = _Masker(text, "typescript")
    index = 0
    length = len(text)
    while index < length:
        found = _INTERESTING.search(text, index)
        if found is None:
            break
        index = found.start()
        char = text[index]
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            end = text.find("\n", index)
            end = length if end == -1 else end
            masker.blank(index, end, _COMMENT_FILL)
            index = end
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            if end == -1:
                masker.blank(index, length, _COMMENT_FILL)
                masker.anomalies.append("unterminated_block_comment")
                break
            masker.blank(index, end + 2, _COMMENT_FILL)
            index = end + 2
            continue
        if char in {"'", '"'}:
            index = _consume_quoted(masker, index, char, allow_newline=False)
            continue
        if char == "`":
            index = _consume_template(masker, index)
            continue
        if char == "/" and _opens_regex(masker.out, index):
            index = _consume_regex(masker, index)
            continue
        index += 1
    return masker.result()


def _opens_regex(out: list[str], index: int) -> bool:
    previous, previous_index = _previous_significant(out, index - 1)
    if previous == "" or previous in _REGEX_PRECEDING_CHARS:
        return True
    if previous == ")":
        # A `)` is usually the end of a call or a parenthesised expression, and
        # `foo(a) / 2` divides. But it is also the end of a control statement's
        # condition, and there a regex validly *begins the body*:
        # `if (ok) /\.registerTool("fake", handler)/.test(value);` is
        # JavaScript, and reading its `/` as division scanned the pattern as
        # code and reported a `fake` tool — a registration invented out of a
        # regex body, which is the one thing this module's masking exists to
        # make impossible. Which of the two it is, is decided by the keyword in
        # front of the matching `(`.
        opener = _matching_open(out, previous_index)
        if opener is None:
            return False
        return _preceding_word(out, opener - 1) in _REGEX_PRECEDING_STATEMENTS
    if previous.isalnum() or previous in "_$":
        return _preceding_word(out, index - 1) in _REGEX_PRECEDING_WORDS
    return False


def _matching_open(out: list[str], close_index: int) -> int | None:
    """Index of the ``(`` matching the ``)`` at ``close_index``."""

    depth = 0
    for index in range(close_index, -1, -1):
        char = out[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                return index
    return None


def _past_escape(text: str, index: int, language: SourceLanguage) -> int:
    """The index just past the escape whose backslash sits at ``index``.

    Two characters, except for a JavaScript line continuation spelled with
    CRLF, which is three: the backslash and one *line terminator sequence*.
    Stepping over two of them leaves the ``\n`` behind, and the scanner then
    ends the string there — so the identical file lost its registration on a
    Git-for-Windows checkout while resolving it on a Unix one.

    Go has no line continuation, and its scanner must keep treating a newline
    as the end of an interpreted string, so this is TypeScript's rule only.
    """

    if language == "typescript" and text[index + 1 : index + 3] == "\r\n":
        return index + 3
    return index + 2


def _consume_quoted(
    masker: _Masker, start: int, quote: str, *, allow_newline: bool
) -> int:
    text = masker.text
    length = len(text)
    index = start + 1
    while index < length:
        char = text[index]
        if char == "\\":
            index = _past_escape(text, index, masker.language)
            continue
        if char == quote:
            masker.record(
                start, index + 1, decode_literal(text[start + 1 : index], masker.language)
            )
            return index + 1
        if char == "\n" and not allow_newline:
            break
        index += 1
    # Unterminated. Blank to the resync point but record no literal, and say so:
    # past here this reader cannot tell code from content.
    end = text.find("\n", start)
    end = length if end == -1 or allow_newline else end
    masker.blank(start, end, _STRING_FILL)
    masker.anomalies.append("unterminated_string")
    return max(end, start + 1)


def _consume_template(masker: _Masker, start: int) -> int:
    """Consume a backtick template literal, tracking ``${…}`` substitutions."""

    text = masker.text
    length = len(text)
    end, substituted = _template_end(text, masker.out, start)
    if end is None:
        masker.blank(start, length, _STRING_FILL)
        masker.anomalies.append("unterminated_string")
        return length
    body = text[start + 1 : end - 1]
    masker.record(
        start, end, None if substituted else decode_literal(body, masker.language)
    )
    return end


def _template_end(
    text: str, out: list[str], start: int
) -> tuple[int | None, bool]:
    """Where the template literal at ``start`` ends, and whether it substitutes.

    ``None`` when it never closes. The second value says whether the *outer*
    template carries a ``${…}``, which is what makes its value non-constant.

    **A `${…}` holds code, so a brace inside a string, a comment, a regex or a
    nested template is not a structural brace.** Counting them made
    ``const msg = `Literal brace: ${"{"}`;`` leave the substitution open, and
    from there the rest of the file was consumed as one unterminated template
    — every registration after that line silently gone, and a workspace that
    declares an MCP dependency reported as "not an agent project" over a brace
    in a string (#485 review).

    Iterative, with one stack entry per open template, because a nested
    template is reached through a substitution and recursion on attacker-shaped
    input is a crash rather than a wrong answer.
    """

    length = len(text)
    index = start + 1
    # One entry per open template: its `${…}` brace depth, 0 in template text.
    depths: list[int] = [0]
    substituted = False
    while index < length and depths:
        char = text[index]
        if char == "\\":
            index = _past_escape(text, index, "typescript")
            continue
        if depths[-1] == 0:
            if char == "$" and text[index + 1 : index + 2] == "{":
                substituted = substituted or len(depths) == 1
                depths[-1] = 1
                index += 2
                continue
            if char == "`":
                depths.pop()
                index += 1
                continue
            index += 1
            continue
        if char in {"'", '"'}:
            index = _skip_quoted(text, index)
            continue
        if char == "`":
            depths.append(0)
            index += 1
            continue
        if char == "/" and text[index + 1 : index + 2] == "/":
            line_end = text.find("\n", index)
            line_end = length if line_end == -1 else line_end
            # Blanked as it is walked, not merely stepped over: the regex
            # heuristic below reads the mask to find the keyword in front of a
            # slash, and a comment still spelled out there hides it.
            out[index:line_end] = _COMMENT_FILL * (line_end - index)
            index = line_end
            continue
        if char == "/" and text[index + 1 : index + 2] == "*":
            close = text.find("*/", index + 2)
            block_end = length if close == -1 else close + 2
            out[index:block_end] = [
                "\n" if character == "\n" else _COMMENT_FILL
                for character in text[index:block_end]
            ]
            index = block_end
            continue
        if char == "/" and _opens_regex(out, index):
            index = _skip_regex(text, index)
            continue
        if char == "{":
            depths[-1] += 1
        elif char == "}":
            depths[-1] -= 1
        index += 1
    return (index if not depths else None), substituted


def _skip_quoted(text: str, start: int) -> int:
    """Index just past a quoted string this reader only needs to walk over."""

    quote = text[start]
    length = len(text)
    index = start + 1
    while index < length:
        char = text[index]
        if char == "\\":
            index = _past_escape(text, index, "typescript")
            continue
        if char == quote:
            return index + 1
        if char == "\n":
            # Unterminated on its line. Resync there rather than swallowing the
            # rest of the substitution.
            return index
        index += 1
    return length


def _skip_regex(text: str, start: int) -> int:
    """Index just past a regex literal, or one past the slash if it is not one."""

    length = len(text)
    index = start + 1
    in_class = False
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "\n":
            return start + 1
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return index + 1
        index += 1
    return start + 1


def _consume_regex(masker: _Masker, start: int) -> int:
    text = masker.text
    length = len(text)
    index = start + 1
    in_class = False
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "\n":
            # Not a regex after all — a lone `/` on a line. Leave it as code.
            return start + 1
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            masker.blank(start, index + 1, _COMMENT_FILL)
            return index + 1
        index += 1
    return start + 1


def _mask_go(text: str) -> MaskedSource:
    masker = _Masker(text, "go")
    index = 0
    length = len(text)
    while index < length:
        found = _INTERESTING.search(text, index)
        if found is None:
            break
        index = found.start()
        char = text[index]
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            end = text.find("\n", index)
            end = length if end == -1 else end
            masker.blank(index, end, _COMMENT_FILL)
            index = end
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            if end == -1:
                masker.blank(index, length, _COMMENT_FILL)
                masker.anomalies.append("unterminated_block_comment")
                break
            masker.blank(index, end + 2, _COMMENT_FILL)
            index = end + 2
            continue
        if char == '"':
            index = _consume_quoted(masker, index, '"', allow_newline=False)
            continue
        if char == "'":
            index = _consume_quoted(masker, index, "'", allow_newline=False)
            continue
        if char == "`":
            end = text.find("`", index + 1)
            if end == -1:
                masker.blank(index, length, _STRING_FILL)
                masker.anomalies.append("unterminated_string")
                break
            masker.record(index, end + 1, text[index + 1 : end])
            index = end + 1
            continue
        index += 1
    return masker.result()


# --- Idiom matchers ----------------------------------------------------------

_TS_MODIFIERS = r"(?:(?:public|private|protected|readonly|override|declare|abstract)\s+)*"
_TS_STATIC_TOOL_NAME_RE = re.compile(
    rf"(?<![\w$])static\s+{_TS_MODIFIERS}toolName\s*(?::[^=;\n]*)?=\s*"
)
_TS_STATIC_OPERATION_TYPE_RE = re.compile(
    rf"(?<![\w$])static\s+{_TS_MODIFIERS}operationType\s*(?::[^=;\n]*)?=\s*"
)
# ``(?<![\w$.])`` and not ``(?<![\w$])``: the character before ``description``
# in ``this.description = "…"`` is a dot, which the narrower lookbehind admits,
# so an assignment inside a method read as the class's description field — and
# ``_first_literal_in`` returns the *first* match in the body, so it could win
# over the real one. ``operationType`` never had the problem because its
# pattern requires a leading ``static``.
_TS_DESCRIPTION_RE = re.compile(
    rf"(?<![\w$.])(?:static\s+)?{_TS_MODIFIERS}description\s*(?::[^=;\n]*)?=\s*"
)
_TS_REGISTER_TOOL_RE = re.compile(r"\.\s*(?:registerTool|tool)\s*\(\s*")
_GO_MUST_TOOL_RE = re.compile(r"(?<![\w])MustTool\s*\(\s*")
_GO_NEW_TOOL_RE = re.compile(r"(?<![\w])NewTool\s*\(\s*")
_GO_TOOL_STRUCT_RE = re.compile(r"(?<![\w])Tool\s*\{")
_GO_STRUCT_NAME_FIELD_RE = re.compile(r"(?<![\w])Name\s*:\s*")


def _matching_close(masked: str, open_index: int, opener: str, closer: str) -> int | None:
    """Index just past the ``closer`` matching the ``opener`` at ``open_index``."""

    depth = 0
    for index in range(open_index, len(masked)):
        char = masked[index]
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _brace_pairs(masked: str) -> list[tuple[int, int]]:
    """Every matched ``{…}`` span, innermost-first for a given opener."""

    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for match in re.finditer(r"[{}]", masked):
        if match.group() == "{":
            stack.append(match.start())
        elif stack:
            pairs.append((stack.pop(), match.start() + 1))
    return pairs


def _enclosing_block(pairs: list[tuple[int, int]], index: int) -> tuple[int, int] | None:
    """The innermost ``{…}`` containing ``index``, as ``(open, close_exclusive)``.

    Takes the file's brace spans rather than re-deriving them, because the
    caller has one site per tool class and re-scanning the whole module for
    each of them is quadratic in a file that declares many.
    """

    best: tuple[int, int] | None = None
    for start, end in pairs:
        if start <= index < end and (best is None or start > best[0]):
            best = (start, end)
    return best


def _resolve_name(value: str | None, found: bool) -> tuple[str | None, str | None]:
    if not found or value is None:
        return None, "name_not_literal"
    if not TOOL_NAME_RE.match(value):
        return None, "implausible_tool_name"
    return value, None


#: Characters that continue an expression rather than beginning a statement.
#: Consulted only *after* a line break, and only for a character the caller's
#: own terminators do not claim: Go ends a struct field with `,` on the next
#: line, and that comma ends the value rather than continuing it.
_EXPRESSION_CONTINUATION = frozenset("+-*/%&|^<>=!?.,([")


def _literal_is_whole_value(
    source: MaskedSource, end: int, terminators: str
) -> bool:
    """Whether the literal ending at ``end`` is the entire value, not part of one.

    ``static toolName = "backup" + SUFFIX`` resolves to a literal this reader
    can see, and reading it as the tool name would publish ``backup`` for a
    tool the server registers under some other name — a fail-open of exactly
    the shape #393 catalogues, where the proof rests on a spelling. The literal
    counts only when the expression ends there: at one of ``terminators``, at
    the end of input, or at a line break (JavaScript inserts the semicolon).
    """

    masked = source.masked
    length = len(masked)
    index = end
    while index < length and masked[index] in " \t\r":
        index += 1
    if index >= length:
        return True
    if masked[index] in terminators:
        return True
    if masked[index] != "\n":
        return False
    # A line break ends the statement only when what follows cannot continue
    # the expression. `static toolName = "safe"` followed by `+ "_delete"` on
    # the next line is one value spelled across two lines, and accepting the
    # first literal publishes `safe` for a tool the server registers as
    # `safe_delete` — a name nobody serves, at `medium` confidence, which is
    # worse than the omission refusing it produces. Comments are already
    # spaces in the mask, so skipping whitespace skips them too.
    while index < length and masked[index].isspace():
        index += 1
    if index >= length:
        return True
    following = masked[index]
    return following in terminators or following not in _EXPRESSION_CONTINUATION


def _call_sites(
    source: MaskedSource, pattern: re.Pattern[str], idiom: str
) -> list[RegistrationSite]:
    """Sites for a ``Name(<literal>, …)`` idiom."""

    sites: list[RegistrationSite] = []
    for match in pattern.finditer(source.masked):
        open_paren = source.masked.rfind("(", match.start(), match.end())
        if open_paren == -1:
            continue
        close = _matching_close(source.masked, open_paren, "(", ")")
        span = (match.start(), close if close is not None else match.end())
        found, value, end = source.literal_at(match.end())
        name, unresolved = _resolve_name(value, found)
        if name is not None:
            # A registration passes the name *and* what to do with it, so the
            # first argument is followed by a comma. Three other things can
            # follow, and they are three different answers:
            #   `)`  — a one-argument call. `map.tool("issues")` is a lookup,
            #          not a registration, and reading it as one is how an
            #          accessor becomes a phantom tool.
            #   `+`  — the literal is part of a computed name, so the name is
            #          not this literal. Unresolved, not resolved-wrongly.
            #   else — same as `+`.
            after = source.skip_space(end)
            following = source.masked[after] if after < len(source.masked) else ""
            if following == ")":
                continue
            if following != ",":
                name, unresolved = None, "name_not_literal"
        if name is None and (
            close is None or not _has_second_argument(source.masked, open_paren, close)
        ):
            # An unresolved site needs the same second argument before it is
            # reported: it is what keeps `map.tool(key)` out of the exclusion
            # ledger.
            continue
        line, column = source.line_column(match.start())
        sites.append(
            RegistrationSite(
                idiom=idiom,
                name=name,
                line=line,
                column=column,
                span=span,
                unresolved_reason=unresolved,
            )
        )
    return sites


def _has_second_argument(masked: str, open_paren: int, close: int) -> bool:
    """Whether the call at ``open_paren`` passes more than one argument."""

    depth = 0
    for index in range(open_paren, close):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 1:
            return True
    return False


def _ts_static_tool_name_sites(source: MaskedSource) -> list[RegistrationSite]:
    sites: list[RegistrationSite] = []
    pairs = _brace_pairs(source.masked)
    for match in _TS_STATIC_TOOL_NAME_RE.finditer(source.masked):
        found, value, end = source.literal_at(match.end())
        name, unresolved = _resolve_name(value, found)
        if name is not None and not _literal_is_whole_value(source, end, ";}"):
            name, unresolved = None, "name_not_literal"
        line, column = source.line_column(match.start())
        block = _enclosing_block(pairs, match.start())
        operation_type: str | None = None
        description: str | None = None
        if block is not None and name is not None:
            operation_type = _first_literal_in(
                source, _TS_STATIC_OPERATION_TYPE_RE, block
            )
            description = _first_literal_in(source, _TS_DESCRIPTION_RE, block)
        # The construct, never the enclosing class body. `block` is the scope
        # the sibling literals are looked up in; using it as the span made
        # `_contains_another_site` read *any* registration written inside the
        # class as "the same registration", so a class whose `toolName` is
        # built at runtime lost its omission the moment it also called
        # `.registerTool(` anywhere in its body — the exact silent miss this
        # input exists to end.
        sites.append(
            RegistrationSite(
                idiom="ts_static_tool_name",
                name=name,
                line=line,
                column=column,
                span=(match.start(), max(end, match.end())),
                description=description,
                operation_type=operation_type,
                unresolved_reason=unresolved,
            )
        )
    return sites


def _first_literal_in(
    source: MaskedSource, pattern: re.Pattern[str], block: tuple[int, int]
) -> str | None:
    start, end = block
    for match in pattern.finditer(source.masked, start, end):
        found, value, literal_end = source.literal_at(match.end())
        if found and value and _literal_is_whole_value(source, literal_end, ";}"):
            return value
    return None


def _go_tool_struct_sites(source: MaskedSource) -> list[RegistrationSite]:
    sites: list[RegistrationSite] = []
    for match in _GO_TOOL_STRUCT_RE.finditer(source.masked):
        open_brace = source.masked.index("{", match.start())
        close = _matching_close(source.masked, open_brace, "{", "}")
        if close is None:
            continue
        if _has_keyed_field(source.masked, open_brace, close):
            sites.extend(
                _go_tool_struct_site(source, match.start(), open_brace, close)
            )
            continue
        # No keyed field at this literal's own level, so it is a composite of
        # elements — `[]mcp.Tool{{Name: "a"}, {Name: "b"}}`. Reading only the
        # outer brace would find the first element's `Name:` two levels down,
        # reject it as a nested field, and report nothing at all: a silent miss
        # of exactly the kind this input exists to close.
        for child_open, child_close in _child_braces(source.masked, open_brace, close):
            sites.extend(
                _go_tool_struct_site(source, child_open, child_open, child_close)
            )
    return sites


def _go_tool_struct_site(
    source: MaskedSource, start: int, open_brace: int, close: int
) -> list[RegistrationSite]:
    field = _GO_STRUCT_NAME_FIELD_RE.search(source.masked, open_brace + 1, close)
    # Only the literal's own `Name:` field, never one belonging to something
    # nested inside it: `mcp.Tool{Annotations: &mcp.ToolAnnotations{Name: …}}`
    # names the annotation, not the tool.
    while field is not None and _brace_depth(source.masked, open_brace, field.start()) != 1:
        field = _GO_STRUCT_NAME_FIELD_RE.search(source.masked, field.end(), close)
    if field is None:
        return []
    found, value, literal_end = source.literal_at(field.end())
    name, unresolved = _resolve_name(value, found)
    if name is not None and not _literal_is_whole_value(source, literal_end, ",}"):
        name, unresolved = None, "name_not_literal"
    line, column = source.line_column(start)
    return [
        RegistrationSite(
            idiom="go_tool_struct",
            name=name,
            line=line,
            column=column,
            span=(start, close),
            description=_go_struct_description(source, open_brace, close),
            unresolved_reason=unresolved,
        )
    ]


_GO_KEYED_FIELD_RE = re.compile(r"(?<![\w])[A-Za-z_]\w*\s*:")


def _has_keyed_field(masked: str, open_brace: int, close: int) -> bool:
    """Whether the literal names fields at its own level (a struct, not a list)."""

    for match in _GO_KEYED_FIELD_RE.finditer(masked, open_brace + 1, close):
        if _brace_depth(masked, open_brace, match.start()) == 1:
            return True
    return False


def _child_braces(masked: str, open_brace: int, close: int) -> list[tuple[int, int]]:
    children: list[tuple[int, int]] = []
    index = open_brace + 1
    while index < close - 1:
        if masked[index] == "{":
            child_close = _matching_close(masked, index, "{", "}")
            if child_close is None or child_close > close:
                break
            children.append((index, child_close))
            index = child_close
            continue
        index += 1
    return children


_GO_STRUCT_DESCRIPTION_FIELD_RE = re.compile(r"(?<![\w])Description\s*:\s*")


def _go_struct_description(
    source: MaskedSource, open_brace: int, close: int
) -> str | None:
    for match in _GO_STRUCT_DESCRIPTION_FIELD_RE.finditer(
        source.masked, open_brace + 1, close
    ):
        if _brace_depth(source.masked, open_brace, match.start()) != 1:
            continue
        found, value, literal_end = source.literal_at(match.end())
        if found and value and _literal_is_whole_value(source, literal_end, ",}"):
            return value
    return None


def _brace_depth(masked: str, open_brace: int, index: int) -> int:
    depth = 0
    for position in range(open_brace, index):
        char = masked[position]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
    return depth


# --- Python: the FastMCP decorator (#484) ------------------------------------
#
# The largest single shape in the #431 survey, and a different extraction
# mechanism from the four idioms above. Three facts drive the design, and all
# three were measured on the surveyed servers rather than assumed:
#
# 1. **The name is usually not written down.** `@mcp.tool()` on `def dbsize()`
#    registers `dbsize`; only `@mcp.tool(name="…")` writes it as a literal. So
#    the name comes from the *decorated function*, and a reader that only
#    looked for a string beside the call would find nothing at all in
#    `redis/mcp-redis` — 55 of 55 registrations.
# 2. **The schema is the signature.** FastMCP builds the tool's input schema
#    out of the annotated parameters, which is real information the TypeScript
#    and Go idioms genuinely do not have.
# 3. **Whether it registers anything is a binding fact.** `@app.tool()` is a
#    tool registration when `app` is a `FastMCP`, and is somebody else's
#    decorator otherwise. Nothing lexical separates the two, so this reader
#    follows the name back to a `FastMCP(...)` construction and refuses — out
#    loud, as a recorded omission — when it cannot. `awslabs/mcp` writes
#    `@self.mcp.tool()` 20 times, on a server passed in as a constructor
#    argument, and that is exactly the shape a proof must decline.
#
# The binding is followed **across modules** because the population requires
# it: `redis/mcp-redis` constructs its server in `src/common/server.py` and
# decorates in `src/tools/*.py`, so a per-file reader proves nothing about the
# repository #484's acceptance criteria name. :class:`PythonServerIndex`
# carries the module-scope constructions of the whole scanned tree, and an
# import is resolved against it by matching path segments — never by importing
# or executing anything.


def _is_python_server_constructor(module: str, symbol: str) -> bool:
    """Whether ``module.symbol`` names a class whose instance is an MCP server."""

    return any(
        symbol == known_symbol
        and (module == known_module or module.startswith(f"{known_module}."))
        for known_module, known_symbol in PYTHON_SERVER_CONSTRUCTORS
    )


def _names_a_python_server_class(text: str) -> bool:
    """Whether ``text`` could construct a server at all.

    A module that constructs one has to name the class, and the class is only
    ever bound by an import this reader checks — so a file naming none of them
    cannot contribute a construction and is never parsed.
    """

    lowered = text.lower()
    return any(token in lowered for token in PYTHON_SERVER_PREFILTER_TOKENS)


def normalized_distribution(requirement: str) -> str | None:
    """The PEP 503 name a requirement string declares, or ``None``.

    Written here rather than reused from discovery's general package-token
    scan because that scan drops exactly the spelling this gate needs:
    ``mcp[cli]>=1.26.0,<2`` — the requirement ``redis/mcp-redis`` and
    ``chroma-core/chroma-mcp`` both declare — carries an extras marker, and a
    token rule admitting only ``[A-Za-z0-9_.-]+`` throws the whole line away.
    """

    text = requirement.strip()
    if not text or text.startswith("#"):
        return None
    match = re.match(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", text)
    if match is None:
        return None
    rest = text[match.end() :].lstrip()
    # Anything may follow a distribution name — extras, a version specifier, a
    # direct reference, an environment marker, a comment — except another word
    # character, which would mean the match stopped inside a token this reader
    # does not understand and the leading run is not the whole name.
    if rest[:1] not in {"", "[", "(", "@", ";", "=", "<", ">", "!", "~", ",", "#"}:
        return None
    return re.sub(r"[-_.]+", "-", match.group(0)).lower()


def declares_python_mcp_framework(requirements: Iterable[str]) -> str | None:
    """The first requirement in ``requirements`` that names an MCP framework."""

    for requirement in requirements:
        name = normalized_distribution(requirement)
        if name is not None and name in PYTHON_FRAMEWORK_PACKAGES:
            return name
    return None


@dataclass(frozen=True)
class _ImportBinding:
    """What one imported name refers to.

    ``symbol`` is ``None`` for ``import a.b`` and ``import a.b as c``, where
    the bound name is an alias for a *module* rather than for something inside
    one.
    """

    module: str
    symbol: str | None
    level: int


class _PythonModule:
    """One parsed module, with every name binding recorded per scope.

    The binding table follows ``google_adk``'s rule (#400 review): a name means
    what the module appears to say only when the module binds it **exactly
    once** in the scope in effect. Two bindings make any resolution a guess
    about which one ran, and a guess is not a proof — so the site becomes a
    recorded omission instead of a catalogued tool.

    Comprehension targets are deliberately not recorded. In Python 3 they bind
    in the comprehension's own scope and cannot shadow the enclosing one, and a
    ``def`` cannot appear inside a comprehension, so there is no decorator for
    them to be in scope for either way.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self._scopes: dict[int, ast.AST] = {}
        self._bindings: dict[tuple[int, str], list[ast.AST]] = {}
        self._imports: dict[tuple[int, str], _ImportBinding] = {}
        #: A ``from x import *`` can rebind any name in the module, so nothing
        #: in it is singly bound any more — including the ``FastMCP`` symbol a
        #: construction rests on. Recorded once and consulted by every
        #: resolution rather than approximated per name (#400 review).
        self.star_import = False
        self._visit(tree, tree)

    # -- Construction ---------------------------------------------------

    def _bind(self, scope: ast.AST, name: str, statement: ast.AST) -> None:
        self._bindings.setdefault((id(scope), name), []).append(statement)

    def _bind_target(
        self, node: ast.AST | None, scope: ast.AST, statement: ast.AST
    ) -> None:
        if isinstance(node, ast.Name):
            self._bind(scope, node.id, statement)
        elif isinstance(node, ast.Tuple | ast.List):
            for element in node.elts:
                self._bind_target(element, scope, statement)
        elif isinstance(node, ast.Starred):
            self._bind_target(node.value, scope, statement)

    def _visit(self, node: ast.AST, scope: ast.AST) -> None:
        self._scopes[id(node)] = scope
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            self._visit_function(node, scope)
            return
        if isinstance(node, ast.ClassDef):
            self._bind(scope, node.name, node)
            for child in (
                *node.decorator_list,
                *node.bases,
                # PEP 695 type parameters — the one child list of a scope
                # node this walk would otherwise skip.
                #
                # Inert for any module Python will run, and knowably so: the
                # only construct that could bind a name here is a walrus, and
                # `compile` refuses one in a TypeVar bound ("named expression
                # cannot be used within a TypeVar bound") even though
                # `ast.parse` accepts it. So a perturbation sweep reports this
                # line untested and is right to. It is here because the rule is
                # "visit every child list of a scope node", and a reader who
                # later adds a construct to `type_params` should not have to
                # rediscover the gap.
                *getattr(node, "type_params", ()),
            ):
                self._visit(child, scope)
            for keyword in node.keywords:
                self._visit(keyword.value, scope)
            for statement in node.body:
                self._visit(statement, node)
            return
        self._record_bindings(node, scope)
        for child in ast.iter_child_nodes(node):
            self._visit(child, scope)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda, scope: ast.AST
    ) -> None:
        # A decorator, a default and an annotation are all evaluated where the
        # ``def`` is written, not inside it. Reading them in the function's own
        # scope would let a parameter named ``mcp`` shadow the module-level
        # server for the very decorator that names it.
        arguments = node.args
        if not isinstance(node, ast.Lambda):
            self._bind(scope, node.name, node)
            for decorator in node.decorator_list:
                self._visit(decorator, scope)
            for parameter in getattr(node, "type_params", ()):
                self._visit(parameter, scope)
            if node.returns is not None:
                self._visit(node.returns, scope)
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self._visit(default, scope)
        for argument in _python_arguments(arguments):
            self._bind(node, argument.arg, argument)
            if argument.annotation is not None:
                self._visit(argument.annotation, scope)
        body = node.body if isinstance(node.body, list) else [node.body]
        for statement in body:
            self._visit(statement, node)

    def _record_bindings(self, node: ast.AST, scope: ast.AST) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._bind_target(target, scope, node)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
            self._bind_target(node.target, scope, node)
        elif isinstance(node, ast.For | ast.AsyncFor):
            self._bind_target(node.target, scope, node)
        elif isinstance(node, ast.withitem):
            self._bind_target(node.optional_vars, scope, node)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                self._bind(scope, node.name, node)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                self._bind_target(target, scope, node)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            # Not a binding, but a declaration that the binding in effect lives
            # in a scope this table did not build for this name. Recording it
            # as one makes the name unprovable, which is the honest answer.
            for name in node.names:
                self._bind(scope, name, node)
        elif isinstance(node, ast.MatchAs | ast.MatchStar):
            if node.name:
                self._bind(scope, node.name, node)
        elif isinstance(node, ast.MatchMapping):
            if node.rest:
                self._bind(scope, node.rest, node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                module = alias.name if alias.asname else bound
                self._bind(scope, bound, node)
                self._imports[(id(scope), bound)] = _ImportBinding(
                    module=module, symbol=None, level=0
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    self.star_import = True
                    continue
                bound = alias.asname or alias.name
                self._bind(scope, bound, node)
                self._imports[(id(scope), bound)] = _ImportBinding(
                    module=node.module or "", symbol=alias.name, level=node.level
                )

    # -- Resolution -----------------------------------------------------

    def _scope_chain(self, node: ast.AST) -> list[ast.AST]:
        """The scopes a name at ``node`` is looked up in, innermost first.

        A class body is searched only when it is the scope the name is written
        in. Python does not close over class scope: a function nested inside a
        method resolves ``mcp`` to the module, never to a class attribute of
        the same name, so keeping the class in the chain made a module-level
        server unreachable from a decorator written one level in — and, the
        other way round, could resolve a class attribute the interpreter never
        consults.
        """

        chain: list[ast.AST] = []
        scope = self._scopes.get(id(node))
        while scope is not None:
            if not isinstance(scope, ast.ClassDef) or not chain:
                chain.append(scope)
            if scope is self.tree:
                break
            scope = self._scopes.get(id(scope))
        return chain

    def binding_of(self, name: str, node: ast.AST) -> tuple[ast.AST, ast.AST] | None:
        """The single binding of ``name`` in effect at ``node``, with its scope."""

        if self.star_import:
            return None
        for scope in self._scope_chain(node):
            bound = self._bindings.get((id(scope), name))
            if bound is None:
                continue
            return (bound[0], scope) if len(bound) == 1 else None
        return None

    def import_of(self, name: str, node: ast.AST) -> _ImportBinding | None:
        """``name``'s import binding at ``node``, when an import is what binds it."""

        resolved = self.binding_of(name, node)
        if resolved is None:
            return None
        binding, scope = resolved
        if not isinstance(binding, ast.Import | ast.ImportFrom):
            return None
        return self._imports.get((id(scope), name))

    def module_named(self, name: str, node: ast.AST) -> str | None:
        """The dotted module ``name`` refers to, when it refers to one.

        Both import forms can name a module: ``import mcp.server.fastmcp``
        binds ``mcp``, and ``from mcp.server import fastmcp`` binds ``fastmcp``
        to the same package one level further down.
        """

        binding = self.import_of(name, node)
        if binding is None or binding.level:
            return None
        if binding.symbol is None:
            return binding.module
        return f"{binding.module}.{binding.symbol}" if binding.module else None

    def is_fastmcp_construction(self, node: ast.AST | None) -> bool:
        """Whether ``node`` is a call that constructs an MCP server."""

        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Name):
            binding = self.import_of(func.id, node)
            return (
                binding is not None
                and binding.level == 0
                and binding.symbol is not None
                and _is_python_server_constructor(binding.module, binding.symbol)
            )
        dotted = _python_dotted_name(func)
        if dotted is None:
            return False
        head, _, attribute = dotted.rpartition(".")
        if not head:
            return False
        root, _, rest = head.partition(".")
        module = self.module_named(root, node)
        if module is None:
            return False
        return _is_python_server_constructor(
            f"{module}.{rest}" if rest else module, attribute
        )

    def server_from_statement(self, statement: ast.AST, name: str) -> bool:
        """Whether ``statement`` binds ``name`` to a server construction."""

        if isinstance(statement, ast.Assign):
            targets = statement.targets
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        else:
            return False
        # Only a plain ``name = FastMCP(...)``, or a chain of them. A tuple
        # unpack binding the same name is binding it to an *element* of
        # something this reader did not evaluate, whatever the right-hand side
        # looks like, so the target has to be the whole left-hand side.
        if name not in {
            target.id for target in targets if isinstance(target, ast.Name)
        }:
            return False
        return self.is_fastmcp_construction(statement.value)

    def module_scope_servers(self) -> frozenset[str]:
        """Module-scope names this module binds to a server construction.

        This is what another module can import. A construction inside a factory
        function is a server too — ``neo4j-contrib/mcp-neo4j`` builds all four
        of its servers that way — but it is not reachable by name from outside,
        so it never enters the index.
        """

        return frozenset(
            name
            for (scope_id, name), bound in self._bindings.items()
            if scope_id == id(self.tree)
            and len(bound) == 1
            and self.server_from_statement(bound[0], name)
        )


def _python_arguments(arguments: ast.arguments) -> list[ast.arg]:
    """Every named parameter, in signature order, variadics included."""

    named = [*arguments.posonlyargs, *arguments.args]
    if arguments.vararg is not None:
        named.append(arguments.vararg)
    named.extend(arguments.kwonlyargs)
    if arguments.kwarg is not None:
        named.append(arguments.kwarg)
    return named


def _python_dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _python_dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def python_server_exports(text: str) -> frozenset[str]:
    """Module-scope names ``text`` binds to an MCP server construction.

    Answers ``frozenset()`` for a module that cannot be parsed. The caller that
    *reads* the module reports the syntax error as an anomaly; reporting it
    twice would put one file's problem into two vocabularies.
    """

    # A cost bound, never a decision. Deleting this line changes no answer the
    # corpus can produce and no answer it should be able to produce: a module
    # that constructs a server has to name the class, so a file this refuses
    # is one the parse below would find nothing in. A perturbation sweep
    # reports it as untested, and that is the correct result for a prefilter —
    # one that changed an answer would be the defect.
    if not _names_a_python_server_class(text):
        return frozenset()
    tree = _parse_python(text)
    if tree is None:
        return frozenset()
    return _PythonModule(tree).module_scope_servers()


def _parse_python(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        # ``ValueError`` for source containing a NUL byte, ``RecursionError``
        # for a deeply nested expression: both are the compiler declining to
        # produce a tree, which is the same fact as a syntax error and has to
        # be one recorded omission rather than an exception out of the walk.
        return None


@dataclass(frozen=True)
class PythonServerIndex:
    """Which modules in the scanned tree export an MCP server by name.

    Built once per scan and consulted per file, because the population needs
    it: ``redis/mcp-redis`` constructs its server in ``src/common/server.py``
    and applies all 55 of its decorators in ``src/tools/*.py``.

    Resolution matches *path segments* against the dotted module a file
    imports from, and it is anchored at neither end. The scanned root is
    wherever ``tool_sources[].path`` points, so a module reached as
    ``src.common.server`` may sit at ``common/server.py`` when the root is
    ``src/`` and at ``src/common/server.py`` when it is the repository — one
    import, two path spellings, and the reader is not told which. A match
    therefore counts when either side's segments end with the other's, and
    only when **exactly one** module in the index matches: two candidates mean
    this reader cannot tell which module was imported, and a guess about that
    is a guess about whether the decorator registers a tool at all.
    """

    modules: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, modules: Iterable[tuple[str, str]]) -> PythonServerIndex:
        """Index ``(relative posix path, source text)`` pairs."""

        indexed: dict[str, frozenset[str]] = {}
        for path, text in modules:
            exports = python_server_exports(text)
            if exports:
                indexed[path] = exports
        return cls(indexed)

    def resolve(self, module_path: str | None, module: str, level: int) -> str | None:
        """The indexed module an import in ``module_path`` refers to."""

        if not self.modules:
            return None
        parts = tuple(part for part in module.split(".") if part)
        if level:
            if module_path is None:
                return None
            package = PurePosixPath(module_path).parent
            for _ in range(level - 1):
                if package == PurePosixPath("."):
                    return None
                package = package.parent
            for candidate in _python_module_paths(package, parts):
                if candidate in self.modules:
                    return candidate
            return None
        if not parts:
            return None
        matches = [
            path
            for path in sorted(self.modules)
            if _python_module_key_matches(_python_module_key(path), parts)
        ]
        return matches[0] if len(matches) == 1 else None


def _python_module_key(path: str) -> tuple[str, ...]:
    """The dotted-path segments a module file would be imported as."""

    pure = PurePosixPath(path)
    parts = pure.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def _python_module_key_matches(key: tuple[str, ...], parts: tuple[str, ...]) -> bool:
    if not key or not parts:
        return False
    if len(key) <= len(parts):
        return parts[-len(key) :] == key
    return key[-len(parts) :] == parts


def _python_module_paths(
    package: PurePosixPath, parts: tuple[str, ...]
) -> tuple[str, ...]:
    if not parts:
        # ``from . import x`` names no module path of its own: what it binds is
        # an attribute of the package's ``__init__``, which is where a name it
        # re-exports would be bound. That is Python's rule and not an
        # approximation of it: after ``from . import server``, the name
        # is the submodule *unless* ``__init__`` binds something else
        # of that name, and a submodule has no ``.tool`` to register
        # with — so the only case worth resolving is the re-export.
        return ((package / "__init__.py").as_posix(),)
    base = package.joinpath(*parts)
    return (
        (base / "__init__.py").as_posix(),
        base.with_suffix(".py").as_posix(),
    )


def _python_sites(
    text: str,
    *,
    module_path: str | None,
    server_index: PythonServerIndex | None,
) -> SourceScanResult:
    """Every MCP server tool decorator in one module."""

    tree = _parse_python(text)
    if tree is None:
        return SourceScanResult(anomalies=("unparseable_python",))
    module = _PythonModule(tree)
    index = server_index or PythonServerIndex()
    offsets: _PythonOffsets | None = None
    sites: list[RegistrationSite] = []
    server_modules: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            receiver = _python_tool_receiver(decorator, module)
            if receiver is None:
                continue
            proven, proving_module = _python_server_receiver(
                module, receiver, index, module_path
            )
            if proving_module is not None:
                server_modules.add(proving_module)
            # Built on the first site, not per file: most modules that pass the
            # `"tool"` prefilter register nothing — a variable of that name, a
            # word in a docstring — and the line table would be built and
            # thrown away for every one of them.
            if offsets is None:
                offsets = _PythonOffsets(text)
            sites.append(
                _python_site(node, decorator, offsets, proven=proven)
            )
    sites.sort(key=lambda site: (site.line, site.column))
    return SourceScanResult(
        sites=tuple(sites), server_modules=tuple(sorted(server_modules))
    )


def _python_tool_receiver(
    decorator: ast.expr, module: _PythonModule
) -> ast.expr | None:
    """The object a ``.tool`` decorator registers on, or ``None``.

    Three spellings, one meaning. FastMCP 2.x accepts a bare ``@mcp.tool``,
    every surveyed server writes ``@mcp.tool()``, and a module may bind the
    bound method to a name of its own first — ``register = mcp.tool`` and then
    ``@register``. The third is followed through exactly **one** hop, the same
    depth the zero-install detector's constant resolution uses: a chain of
    aliases is unbounded, and each extra hop buys a shape nobody was measured
    writing while widening what can be mistaken for a registration.
    """

    expression = decorator.func if isinstance(decorator, ast.Call) else decorator
    if (
        isinstance(expression, ast.Attribute)
        and expression.attr == PYTHON_TOOL_DECORATOR_ATTR
    ):
        return expression.value
    if not isinstance(expression, ast.Name):
        return None
    resolved = module.binding_of(expression.id, expression)
    if resolved is None:
        return None
    binding, _scope = resolved
    # Only ``alias = <something>.tool``. Every other decorator in the language
    # binds to something else — ``@staticmethod`` to an import, ``@deco`` to a
    # ``def`` — so this cannot turn an ordinary decorator into a registration.
    if not isinstance(binding, ast.Assign) or not all(
        isinstance(target, ast.Name) for target in binding.targets
    ):
        return None
    value = binding.value
    if (
        isinstance(value, ast.Attribute)
        and value.attr == PYTHON_TOOL_DECORATOR_ATTR
    ):
        return value.value
    return None


def _python_server_receiver(
    module: _PythonModule,
    receiver: ast.expr,
    index: PythonServerIndex,
    module_path: str | None,
) -> tuple[bool, str | None]:
    """Whether ``receiver`` is a proven MCP server, and what proved it.

    A bare name is the only shape a proof is attempted for. ``@self.mcp.tool``
    reaches through an attribute this reader would have to know the type of,
    and ``awslabs/mcp`` writes it on a server handed in as a constructor
    argument — so the receiver is a server there and this reader still cannot
    show it, which is what the recorded omission says.
    """

    if not isinstance(receiver, ast.Name):
        return False, None
    resolved = module.binding_of(receiver.id, receiver)
    if resolved is None:
        return False, None
    binding, _scope = resolved
    if module.server_from_statement(binding, receiver.id):
        return True, None
    # A function-local ``from .server import mcp`` binds the same name from the
    # same module as one at the top of the file, and resolving it is the same
    # question. Restricting the proof to module scope refused a shape the
    # index can answer, for no reason a test could state.
    if not isinstance(binding, ast.ImportFrom):
        return False, None
    imported = module.import_of(receiver.id, receiver)
    if imported is None or imported.symbol is None:
        return False, None
    target = index.resolve(module_path, imported.module, imported.level)
    if target is None or imported.symbol not in index.modules.get(target, frozenset()):
        return False, None
    return True, target


def _python_site(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    decorator: ast.expr,
    offsets: _PythonOffsets,
    *,
    proven: bool,
) -> RegistrationSite:
    line, column = decorator.lineno, offsets.column(decorator)
    # The span is the decorator expression, not the function it decorates.
    # ``_contains_another_site`` drops an unresolved site that contains a
    # resolved one, and a decorated function's body can hold a nested
    # registration of its own — so a span covering the body would silently
    # delete the outer omission. A decorator expression cannot contain a
    # registration, which is what makes it the construct that registers.
    span = (offsets.offset(decorator), offsets.end_offset(decorator))
    if not proven:
        return RegistrationSite(
            idiom="py_fastmcp_decorator",
            name=None,
            line=line,
            column=column,
            span=span,
            unresolved_reason="server_binding_not_proven",
        )
    name, unresolved = _python_tool_name(node, decorator)
    return RegistrationSite(
        idiom="py_fastmcp_decorator",
        name=name,
        line=line,
        column=column,
        span=span,
        description=_python_tool_description(node, decorator),
        unresolved_reason=unresolved,
        parameters=_python_signature(node),
        returns=_python_annotation(node.returns),
        proves_server=True,
    )


def _python_tool_name(
    node: ast.FunctionDef | ast.AsyncFunctionDef, decorator: ast.expr
) -> tuple[str | None, str | None]:
    """The registered name, or why it could not be read.

    FastMCP takes the name from ``name=``, then from a single positional
    argument, and defaults to the decorated function's. The default is not a
    guess — it is the framework's documented rule and the spelling 55 of 55
    ``redis/mcp-redis`` registrations use — but a ``name=`` this reader cannot
    resolve is never *replaced* by it: ``neo4j-contrib/mcp-neo4j`` registers
    ``namespace_prefix + "delete_instance"``, and answering ``delete_instance``
    there would publish a name that server does not serve.
    """

    if isinstance(decorator, ast.Call):
        given = _python_keyword(decorator, "name")
        if given is None and decorator.args:
            given = decorator.args[0]
        if given is not None:
            return _resolve_name(_python_string(given), True)
    return _resolve_name(node.name, True)


def _python_tool_description(
    node: ast.FunctionDef | ast.AsyncFunctionDef, decorator: ast.expr
) -> str | None:
    """The description FastMCP would register: ``description=`` or the docstring."""

    if isinstance(decorator, ast.Call):
        given = _python_keyword(decorator, "description")
        if given is not None:
            return _python_string(given)
    try:
        return ast.get_docstring(node, clean=True)
    except (TypeError, ValueError):  # pragma: no cover - malformed docstring node
        return None


def _python_keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _python_string(node: ast.expr) -> str | None:
    """The value of ``node`` when it is a whole string literal.

    An f-string, a concatenation and a ``.format`` call all resolve to
    something only at run time, and this returns ``None`` for each — the same
    answer :func:`_literal_is_whole_value` gives the lexed languages when a
    literal is only *part* of the value.
    """

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _python_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[SignatureParameter, ...]:
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(arguments.defaults)
    )
    defaults.extend(arguments.defaults)
    parameters = [
        SignatureParameter(
            name=argument.arg,
            annotation=_python_annotation(argument.annotation),
            required=default is None,
        )
        for argument, default in zip(positional, defaults, strict=True)
    ]
    parameters.extend(
        SignatureParameter(
            name=argument.arg,
            annotation=_python_annotation(argument.annotation),
            required=default is None,
        )
        for argument, default in zip(
            arguments.kwonlyargs, arguments.kw_defaults, strict=True
        )
    )
    # ``*args`` and ``**kwargs`` are deliberately absent: they are not schema
    # properties, and a tool that has them takes arguments this reader cannot
    # enumerate rather than one parameter named ``kwargs``.
    return tuple(parameters)


def _python_annotation(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError, RecursionError):  # pragma: no cover
        return None


class _PythonOffsets:
    """Character offsets for ``ast`` positions.

    ``col_offset`` is a **UTF-8 byte** offset into the line, while every other
    offset this module publishes is a character offset into the text. A source
    line containing a non-ASCII character makes the two differ, so the
    conversion is done rather than assumed.
    """

    def __init__(self, text: str) -> None:
        self._lines = text.splitlines(keepends=True)
        self._starts: list[int] = []
        offset = 0
        for line in self._lines:
            self._starts.append(offset)
            offset += len(line)
        self._length = offset

    def _character_column(self, line: int, byte_column: int) -> int:
        if not 1 <= line <= len(self._lines):
            return 0
        raw = self._lines[line - 1].encode("utf-8")[:byte_column]
        return len(raw.decode("utf-8", errors="ignore"))

    def offset(self, node: ast.AST) -> int:
        line = getattr(node, "lineno", 1)
        if not 1 <= line <= len(self._starts):
            return self._length
        return self._starts[line - 1] + self._character_column(
            line, getattr(node, "col_offset", 0)
        )

    def end_offset(self, node: ast.AST) -> int:
        line = getattr(node, "end_lineno", None)
        if line is None or not 1 <= line <= len(self._starts):
            return self._length
        return self._starts[line - 1] + self._character_column(
            line, getattr(node, "end_col_offset", 0)
        )

    def column(self, node: ast.AST) -> int:
        """The 1-based character column, matching :meth:`MaskedSource.line_column`."""

        return self._character_column(
            getattr(node, "lineno", 1), getattr(node, "col_offset", 0)
        ) + 1


def scan_source(
    text: str,
    language: SourceLanguage,
    *,
    module_path: str | None = None,
    server_index: PythonServerIndex | None = None,
) -> SourceScanResult:
    """Find every registration site in one file.

    An unresolved site is dropped when a resolved one sits inside it. The
    wrapper shape is real and common — ``NewTool(metadata, mcp.Tool{Name:
    "issue_read"}, …)`` in ``github/github-mcp-server`` is 132 of them — and
    reporting the wrapper's non-literal first argument as an unenumerated tool
    would fill the exclusion ledger with omissions for tools the very same call
    names one argument later.

    ``module_path`` and ``server_index`` are the Python idiom's cross-module
    binding evidence and are ignored by the lexed languages, which resolve a
    name against nothing outside the file. Omitting them is not an error: it
    reads the module on its own, which proves every construction written in it
    and no import of one — the reason they are optional is that most callers
    (and every corpus case) ask about one file.
    """

    if PREFILTER_TOKEN not in text.lower():
        return SourceScanResult()
    if language == "python":
        return _python_sites(
            text, module_path=module_path, server_index=server_index
        )
    source = mask_source(text, language)
    sites: list[RegistrationSite] = []
    if language == "typescript":
        sites.extend(_ts_static_tool_name_sites(source))
        sites.extend(_call_sites(source, _TS_REGISTER_TOOL_RE, "ts_sdk_register_tool"))
    else:
        sites.extend(_call_sites(source, _GO_MUST_TOOL_RE, "go_must_tool"))
        sites.extend(_call_sites(source, _GO_NEW_TOOL_RE, "go_new_tool"))
        sites.extend(_go_tool_struct_sites(source))

    kept = [
        site
        for site in sites
        if site.name is not None or not _contains_another_site(site, sites)
    ]
    kept.sort(key=lambda site: (site.line, site.column, site.idiom))
    return SourceScanResult(sites=tuple(kept), anomalies=source.anomalies)


def _contains_another_site(
    site: RegistrationSite, sites: list[RegistrationSite]
) -> bool:
    """Whether a nested site describes the same registration as ``site``.

    Only unresolved sites are tested. A wrapper whose own first argument is not
    a literal is not a second registration; it is the outside of the one the
    nested site already reports, resolved or not. Reporting both turned
    ``NewTool(meta, mcp.Tool{Name: name})`` into two omissions for one tool.

    This is sound only because every ``span`` is the *construct* that
    registers — a call and its argument list, a composite literal, a field's
    own statement. A span standing for a lookup *scope* would make any
    registration written inside that scope suppress the site, which is a
    different relationship entirely.
    """

    start, end = site.span
    return any(
        start < other.span[0] and other.span[1] <= end
        for other in sites
        if other is not site
    )
