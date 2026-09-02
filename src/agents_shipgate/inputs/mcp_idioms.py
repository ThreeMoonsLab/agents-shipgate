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
vendors. Python's FastMCP decorator is the largest single shape in that survey
and is deliberately absent: it is a different extraction mechanism (a real AST,
a name that defaults to the function's, a schema that comes from the signature)
and per #393 each mechanism needs its own adversarial probe list before it can
claim anything.

**Honest about what it proved.** Every observation records which idiom matched.
A literal at a registration site is ``medium`` confidence — a committed export
stays ``high`` and remains the better route wherever both exist. A name this
reader cannot resolve to a literal is reported as *unenumerated*, never dropped:
it becomes a typed omission that the exclusion ledger (#403) accounts for and
that holds the whole file's surface at ``partial``.

Reading is done over a **masked** copy of the source, in which comments and
string bodies have been overwritten (see :func:`mask_source`). A registration
site can therefore never be found inside a comment or inside another string,
and a name is a name only when the masking pass recorded a real literal at that
offset. Where masking cannot complete — an unterminated string or block comment
— the file is reported partial rather than read as though the rest were code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

#: The version of this registry's published vocabulary — the idiom ids, and the
#: diff tokens the trigger catalog renders from them. Bumped when a consumer
#: pinning an idiom id would have to change; adding an idiom bumps it, because
#: the trigger catalog's token list is derived from this registry and a consumer
#: that mirrors the list has to re-read it.
IDIOM_REGISTRY_VERSION = "1"

SourceLanguage = Literal["typescript", "go"]

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
}

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

#: Directory names never walked for registration sites. Vendored dependencies
#: and build output contain other people's registrations, and reporting them as
#: this repository's surface is the same over-claim as reading a lockfile.
SKIP_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".next",
        ".nuxt",
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
    ".test.ts",
    ".test.js",
    ".test.mts",
    ".test.mjs",
    ".spec.ts",
    ".spec.js",
    ".spec.mts",
    ".spec.mjs",
)

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


@dataclass(frozen=True)
class SourceScanResult:
    """What one file yielded.

    ``anomalies`` are masking failures — an unterminated string or block
    comment. They are separate from an unresolved site because they are a fact
    about the *file*, not about one registration: past the anomaly this reader
    cannot tell code from content, so a site it did not find there proves
    nothing.
    """

    sites: tuple[RegistrationSite, ...] = ()
    anomalies: tuple[str, ...] = ()


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
#: JavaScript adds a backtick, ``\0`` for NUL, and a line continuation.
_TYPESCRIPT_ESCAPES = {**_SHARED_ESCAPES, "`": "`", "\n": ""}
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


def _preceding_word(text: str, index: int) -> str:
    while index >= 0 and text[index].isspace():
        index -= 1
    end = index + 1
    while index >= 0 and (text[index].isalnum() or text[index] in "_$"):
        index -= 1
    return text[index + 1 : end]


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
        if char == "/" and _opens_regex(masker.out, text, index):
            index = _consume_regex(masker, index)
            continue
        index += 1
    return masker.result()


def _opens_regex(out: list[str], text: str, index: int) -> bool:
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
        return _preceding_word(text, opener - 1) in _REGEX_PRECEDING_STATEMENTS
    if previous.isalnum() or previous in "_$":
        return _preceding_word(text, index - 1) in _REGEX_PRECEDING_WORDS
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


def _consume_quoted(
    masker: _Masker, start: int, quote: str, *, allow_newline: bool
) -> int:
    text = masker.text
    length = len(text)
    index = start + 1
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
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
    index = start + 1
    substituted = False
    depth = 0
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if depth == 0 and char == "$" and index + 1 < length and text[index + 1] == "{":
            substituted = True
            depth = 1
            index += 2
            continue
        if depth > 0:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
            continue
        if char == "`":
            body = text[start + 1 : index]
            masker.record(
                start,
                index + 1,
                None if substituted else decode_literal(body, masker.language),
            )
            return index + 1
        index += 1
    masker.blank(start, length, _STRING_FILL)
    masker.anomalies.append("unterminated_string")
    return length


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


def _enclosing_blocks(
    pairs: list[tuple[int, int]], indexes: list[int]
) -> list[tuple[int, int] | None]:
    """Resolve ordered source positions to their innermost brace spans.

    Both inputs come from left-to-right scans.  Keeping the still-open spans
    on a stack visits each brace pair and registration site once; looking
    through every pair for every site made a flat generated file quadratic.
    """

    ordered_pairs = sorted(pairs)
    active: list[tuple[int, int]] = []
    resolved: list[tuple[int, int] | None] = []
    pair_index = 0
    for index in indexes:
        while active and active[-1][1] <= index:
            active.pop()
        while (
            pair_index < len(ordered_pairs)
            and ordered_pairs[pair_index][0] <= index
        ):
            pair = ordered_pairs[pair_index]
            while active and active[-1][1] <= pair[0]:
                active.pop()
            active.append(pair)
            pair_index += 1
        while active and active[-1][1] <= index:
            active.pop()
        resolved.append(active[-1] if active else None)
    return resolved


def _resolve_name(value: str | None, found: bool) -> tuple[str | None, str | None]:
    if not found or value is None:
        return None, "name_not_literal"
    if not TOOL_NAME_RE.match(value):
        return None, "implausible_tool_name"
    return value, None


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

    index = end
    length = len(source.masked)
    while index < length and source.masked[index] in " \t\r":
        index += 1
    if index >= length:
        return True
    return source.masked[index] in terminators or source.masked[index] == "\n"


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
    matches = list(_TS_STATIC_TOOL_NAME_RE.finditer(source.masked))
    blocks = _enclosing_blocks(pairs, [match.start() for match in matches])
    for match, block in zip(matches, blocks, strict=True):
        found, value, end = source.literal_at(match.end())
        name, unresolved = _resolve_name(value, found)
        if name is not None and not _literal_is_whole_value(source, end, ";}"):
            name, unresolved = None, "name_not_literal"
        line, column = source.line_column(match.start())
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


def scan_source(text: str, language: SourceLanguage) -> SourceScanResult:
    """Find every registration site in one file.

    An unresolved site is dropped when a resolved one sits inside it. The
    wrapper shape is real and common — ``NewTool(metadata, mcp.Tool{Name:
    "issue_read"}, …)`` in ``github/github-mcp-server`` is 132 of them — and
    reporting the wrapper's non-literal first argument as an unenumerated tool
    would fill the exclusion ledger with omissions for tools the very same call
    names one argument later.
    """

    if PREFILTER_TOKEN not in text.lower():
        return SourceScanResult()
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
