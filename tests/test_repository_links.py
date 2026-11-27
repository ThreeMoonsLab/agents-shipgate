"""Every relative link and anchor in the repository's Markdown resolves (#1039).

``tests/test_docs_links.py`` checks that a hand-picked list of documents links
to files that exist. It reads no anchor, and a document off its list is not
read at all, so the release records under ``docs/changelog/`` carried 25 dead
links (prose moved verbatim out of ``CHANGELOG.md``, its root-relative targets
no longer resolving two directories down) and ``CHANGELOG.md`` linked a
``STABILITY.md`` anchor that the title's version stamp had renamed. This
module reads every tracked Markdown file.

What it checks, and what it does not:

* **Sources** are the Markdown files ``git ls-files`` lists, so an untracked
  scratch note never fails the suite for a reason nobody else can reproduce.
* **Links** are inline links and images (``[text](target)``), reference
  definitions (``[label]: target``) and HTML ``href``/``src`` attributes.
  Fenced and indented code blocks, inline code spans, HTML comments and YAML
  front matter are not rendered as links, so they are skipped, and Markdown
  inside a raw HTML block is text (its tags' attributes are read). A target
  with a URL scheme (``https:``, ``mailto:``) or ``//`` is external and not
  fetched.
* **Targets** resolve the way GitHub resolves them: relative to the linking
  file's directory, or to the repository root when they begin with ``/``. A
  target must be a tracked file, or a directory holding one, spelled with
  the tracked case. A path that exists on disk but is untracked or ignored
  is reported as broken: the link is dead on GitHub and in every other
  clone, so ``git add`` it or drop it. A target that leaves the repository
  is broken too.
* **Anchors** in a ``.md`` target are checked against the anchors this module
  derives for that file the way GitHub does: each heading's slug (see
  :func:`github_heading_slug`) with ``-1``, ``-2`` for repeats, and every
  explicit ``id=``/``name=`` attribute. A fragment on any other file (a
  ``#L12`` line anchor on a ``.py`` file) is not checked.

The parser is a reading of GitHub's renderer, not a Markdown implementation.
It was checked against GitHub (``gh api markdown -f mode=markdown``, with
front matter blanked as the file view does) on 2026-11-27: for each of the 354
tracked Markdown files, the relative link destinations it extracts and the
ordered heading anchors it derives equal GitHub's. The tests below pin the
cases that check and its reviews covered. Known shapes it reads
differently, none of them in the repository then:

* a link inside another link's text (``[a [b](c) d](e)``) also yields ``e``;
* blockquotes are tracked through their markers, not as containers: a raw
  HTML block inside ``>`` is read as prose (``> <div>`` does not open a
  block), and whether a ``>`` line indented four columns past its marker is
  code is judged from the previous line alone;
* a heading or code span inside a list item indented four or more columns,
  a setext heading inside a container, a ``- - -`` thematic break (read as a
  list item), and a table row that does not begin with ``|`` follow the
  simpler rules here;
* a four-column indented line after ``***``, after a table, or after a list
  item that interrupts a paragraph (``Para\\n2. two``, a blank line, then
  the indented line) is read as prose where GitHub reads code;
* the ``href``/``src`` of a tag GitHub's sanitizer drops (``<source src>``
  outside ``<picture>``) is still checked.
"""

from __future__ import annotations

import html
import posixpath
import re
import subprocess
import unicodedata
from bisect import bisect_right
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from urllib.parse import unquote

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

MARKDOWN_SUFFIXES = (".md", ".markdown")

_FENCE_OPEN = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
# A blockquote marker at any indentation; inside an open fence, it counts the quote depth.
_QUOTE_PREFIX = re.compile(r"^[ \t]*>[ \t]?")
# A blockquote marker or list item marker in front of a heading: `> ## A`, `- ## B`.
_CONTAINER_MARKER = re.compile(r"^ {0,3}(?:>[ ]?|(?:[-+*]|\d{1,9}[.)])[ \t]+)")
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_NOT_A_PARAGRAPH = re.compile(r"^ {0,3}(?:[#>|]|[-+*][ \t]|\d+[.)][ \t]|$)")
_INLINE_CODE_OR_COMMENT = re.compile(r"\\[\\`<]|`+|<!--")
# A line that starts a new block, so no code span reaches across it.
_BLOCK_START = re.compile(
    r"^(?: {0,3}#{1,6}[ \t]|[ \t]*(?:[-+*]|\d{1,9}[.)])[ \t]|[ \t]*\|)", re.MULTILINE
)
_HEADING_OR_TABLE_ROW = re.compile(r" {0,3}#{1,6}[ \t]|[ \t]*\|")
_HTML_COMMENT = re.compile(r"<!--(?:>|->|.*?-->)", re.DOTALL)  # <!--> and <!---> are whole
# Raw HTML block starts (CommonMark 0.29, which GitHub's cmark-gfm follows),
# matched after the line's indentation, which must be under four columns.
_HTML_BLOCK_RAW = re.compile(r"<(script|pre|style)(?:[\s>]|$)", re.IGNORECASE)
_HTML_BLOCK_TAG = re.compile(
    r"</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col"
    r"|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame"
    r"|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav"
    r"|noframes|ol|optgroup|option|p|param|section|source|summary|table|tbody|td|tfoot|th"
    r"|thead|title|tr|track|ul)(?:[\s/>]|$)",
    re.IGNORECASE,
)
_HTML_ATTRIBUTE = (
    r"(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:[^\s\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)"
)
_HTML_BLOCK_LONE_TAG = re.compile(
    rf"(?:<[A-Za-z][A-Za-z0-9-]*{_HTML_ATTRIBUTE}*\s*/?>|</[A-Za-z][A-Za-z0-9-]*\s*>)[ \t]*$"
)
_LIST_ITEM = re.compile(r"([-+*]|\d{1,9}[.)])([ \t]+|$)")
_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*|[^\s<>@]+@[^\s<>@]+)>")
_HTML_TAG = re.compile(r"<[A-Za-z][^<>]*>|</[A-Za-z][^<>]*>")
_HTML_ANCHOR_ATTRIBUTE = re.compile(
    r"<[A-Za-z][^<>]*?\s(?:id|name)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))",
    re.IGNORECASE,
)
_HTML_LINK_ATTRIBUTE = re.compile(
    r"<[A-Za-z][^<>]*?\s(?:href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))",
    re.IGNORECASE,
)
_REFERENCE_DEFINITION = re.compile(
    r"^[ \t>]*\[(?!\^)(?:[^\[\]\\]|\\.)+\]:[ \t]*(?:\n[ \t]*)?(<[^<>\n]*>|\S+)", re.MULTILINE
)
_LINK_TITLE_AND_CLOSE = re.compile(
    r"[ \t]*\n?[ \t]*(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\))?[ \t]*\n?[ \t]*\)"
)
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_BACKSLASH_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
# A paired `_x_` or `__x__` is emphasis; a lone or intraword `_` is literal.
_UNDERSCORE_EMPHASIS = re.compile(r"(?<![A-Za-z0-9_])(_{1,2})(?=\S)(.+?)(?<=\S)\1(?![A-Za-z0-9_])")
# Ruby's \p{Word}, which GitHub's heading filter keeps: letters, marks,
# decimal digits and connector punctuation.
_SLUG_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo", "Mn", "Mc", "Me", "Nd", "Pc"})


@dataclass(frozen=True)
class BrokenLink:
    source: str
    line: int
    target: str
    reason: str

    def __str__(self) -> str:
        return f"{self.source}:{self.line}: {self.target} -> {self.reason}"


# ---------------------------------------------------------------------------
# Markdown structure
# ---------------------------------------------------------------------------


def _blank(text: str) -> str:
    """The same text with every character but newlines turned into spaces."""

    return re.sub(r"[^\n]", " ", text)


def _front_matter_end(lines: list[str]) -> int:
    """Index of the first line after a leading YAML front matter block."""

    if not lines or lines[0].rstrip() != "---":
        return 0
    for index in range(1, len(lines)):
        if lines[index].rstrip() in {"---", "..."}:
            return index + 1
    return 0


_ENDS_ON_ITS_OWN_LINE = "\0"


def _advance(column: int, whitespace: str) -> int:
    """The column after ``whitespace`` that starts at ``column``; a tab stops at a multiple of 4."""

    for ch in whitespace:
        column = column + 4 - column % 4 if ch == "\t" else column + 1
    return column


def _columns(indentation: str) -> int:
    """The width of leading whitespace, with a tab advancing to the next multiple of 4."""

    return _advance(0, indentation)


def _strip_containers(line: str, start: int = 0) -> tuple[str, int, int, int, list[int]]:
    """The line after the blockquote and list item markers that open it.

    Returns ``(rest, column, base, quotes, items)``: the text after the
    markers, the column it starts at, the column just after the last ``>``
    (0 without one), how many ``>`` markers it passed, and the content column
    of each list item it opens before any ``>``. A marker may be indented up
    to three columns past the content column ``start`` of the list item the
    line is in (so ``    - b`` under ``- a`` is a nested item), and later
    markers up to three past the one before. One column of whitespace after
    ``>`` belongs to the marker; a list item's content starts after its
    marker and gap, or one column after the marker when the gap is five or
    more (the rest is then indented code).
    """

    rest, column, base, quotes = line, 0, 0, 0
    items: list[int] = []
    reference = start
    while True:
        text = rest.lstrip(" \t")
        lead = _advance(column, rest[: len(rest) - len(text)])
        if lead - max(reference, column) > 3:
            return rest, column, base, quotes, items
        reference = 0
        if text.startswith(">"):
            after = text[1:]
            body = after.lstrip(" \t")
            width = _advance(lead + 1, after[: len(after) - len(body)]) - (lead + 1)
            column = lead + 1 + min(width, 1)
            rest = " " * max(width - 1, 0) + body
            base, quotes = column, quotes + 1
            continue
        marker = _LIST_ITEM.match(text)
        if marker is None:
            return rest, column, base, quotes, items
        marker_end = lead + len(marker.group(1))
        body = text[marker.end() :]
        width = _advance(marker_end, marker.group(2)) - marker_end
        if not body or width > 4:
            content = marker_end + 1
            rest = " " * (width - 1) + body if body else ""
        else:
            content = marker_end + width
            rest = body
        column = content
        if not quotes:
            items.append(content)
        if not body:
            return rest, column, base, quotes, items


# How a raw HTML block that starts with each opener ends (CommonMark types 2-5).
_HTML_BLOCK_OPENERS = (("<!--", 2, "-->"), ("<?", 2, "?>"), ("<![CDATA[", 9, "]]>"))


def _html_block(content: str, after_paragraph: bool) -> str | None:
    """What ends a raw HTML block that starts this line, or ``None``.

    ``content`` is the line without its indentation. CommonMark 0.29's kinds,
    as GitHub's cmark-gfm renders them: ``<script>``/``<pre>``/``<style>``
    run to their closing tag; a comment to ``-->`` (``<!-->`` is a whole
    one), ``<?`` to ``?>``, ``<![CDATA[`` to ``]]>`` and ``<!X`` to ``>``; a
    block-level tag (``<details>``, ``</div>``…) to the next blank line
    (``""``); and any other complete tag alone on a line also to a blank line,
    but only where it does not continue a paragraph.
    """

    if match := _HTML_BLOCK_RAW.match(content):
        closing = f"</{match.group(1).lower()}>"
        return _ENDS_ON_ITS_OWN_LINE if closing in content.lower() else closing
    for opener, searched_from, closing in _HTML_BLOCK_OPENERS:
        if content.startswith(opener):
            return _ENDS_ON_ITS_OWN_LINE if closing in content[searched_from:] else closing
    if re.match(r"<![A-Za-z]", content):
        return _ENDS_ON_ITS_OWN_LINE if ">" in content[2:] else ">"
    if _HTML_BLOCK_TAG.match(content):
        return ""
    if _HTML_BLOCK_LONE_TAG.match(content) and not after_paragraph:
        return ""
    return None


def _block_masks(lines: list[str]) -> tuple[list[bool], list[bool]]:
    """Which lines are code, and which are raw HTML blocks.

    Code is front matter, fenced code and indented code. A fence closes on a
    line of the same character at least as long, as CommonMark says; an
    unclosed fence runs to the end of its list item, blockquote or file. A
    closing line indented four or more columns past the opening one is
    content. Indentation is measured from the content column of the list item
    a line belongs to, so a fence inside a list item is a fence, and a line
    four or more columns past it is indented code unless it continues a
    paragraph. A fence or indented code may also follow the line's own
    blockquote and list markers (``2. ```bash``, ``> - ```````, ``>     code``).
    Inside a raw HTML block a fence opens nothing, Markdown is not rendered,
    and its tags' attributes are.
    """

    code = [False] * len(lines)
    raw_html = [False] * len(lines)
    for index in range(_front_matter_end(lines)):
        code[index] = True
    open_fence: str | None = None
    open_indent = 0
    open_quote_depth = 0
    open_item_column = 0
    open_quoted_item_column = 0
    html_until: str | None = None  # "" ends at a blank line
    list_items: list[int] = []  # the content column of each open list item
    previous = "blank"  # what the last line was: blank, paragraph, code or other
    for index, line in enumerate(lines):
        if code[index]:
            continue
        stripped = line.strip()
        content = line.lstrip(" \t")
        indent = _columns(line[: len(line) - len(content)])
        # A fence inside a blockquote is read without its markers, and ends with it.
        fence_line = line
        quote_body = line
        quote_depth = 0
        while quote := _QUOTE_PREFIX.match(fence_line):
            fence_line = fence_line[quote.end() :]
            quote_depth += 1
            if quote_depth == open_quote_depth:
                quote_body = fence_line
        if open_fence is not None:
            unquoted = quote_body.lstrip(" \t")
            quote_indent = _columns(quote_body[: len(quote_body) - len(unquoted)])
            if (
                quote_depth < open_quote_depth
                or (stripped and indent < open_item_column)
                or (unquoted and quote_indent < open_quoted_item_column)
            ):
                open_fence = None
                previous = "other"
        if open_fence is not None:
            code[index] = True
            # A deeper quote marker is content within the open fence.
            closing_line = fence_line if quote_depth == open_quote_depth else line
            closing = closing_line.lstrip(" \t")
            closing_indent = _columns(closing_line[: len(closing_line) - len(closing)])
            if (
                closing.strip()
                and set(closing.strip()) == {open_fence[0]}
                and len(closing.strip()) >= len(open_fence)
                and closing_indent < open_indent + 4
            ):
                open_fence = None
                previous = "other"
            continue
        if html_until is not None:
            if html_until == "" and not stripped:
                html_until = None
                previous = "blank"
                continue
            raw_html[index] = True
            if html_until and html_until in line.lower():
                html_until = None
                previous = "other"
            continue
        if not stripped:
            previous = "blank"
            continue
        item = _LIST_ITEM.match(content)
        lazy = (
            previous == "paragraph"
            and not item
            and _ATX_HEADING.match(content) is None
            and not _FENCE_OPEN.match(content)
            and _html_block(content, True) is None
        )
        if not lazy:
            # Not a lazy paragraph continuation, so it closes the list items it is left of.
            while list_items and indent < list_items[-1]:
                list_items.pop()
        relative = indent - (list_items[-1] if list_items else 0)
        if relative >= 4:
            if previous == "paragraph":
                continue  # an indented continuation line of the paragraph
            code[index] = True
            previous = "code"
            continue
        # What follows the line's own blockquote and list markers: a fence on
        # the same line as `2.` or `>` is a fence (`2. ```bash`, `> - ````).
        inner, inner_column, base, quotes, items = _strip_containers(
            line, list_items[-1] if list_items else 0
        )
        list_items.extend(items)
        inner_text = inner.lstrip(" \t")
        inner_indent = _advance(inner_column, inner[: len(inner) - len(inner_text)]) - inner_column
        if not inner_text:
            previous = "blank" if quotes else "other"  # `>` alone, or an empty item
            continue
        if (quotes or items) and inner_indent >= 4:
            # Four columns past a marker on this line: indented code in that quote or item.
            if previous == "paragraph" and not items:
                continue
            code[index] = True
            previous = "code"
            continue
        fence = _FENCE_OPEN.match(inner_text)
        if fence and not (fence.group("fence")[0] == "`" and "`" in fence.group("info")):
            open_fence = fence.group("fence")
            open_indent = inner_column + inner_indent - base
            open_quote_depth = quotes
            open_item_column = list_items[-1] if list_items else 0
            open_quoted_item_column = inner_column - base if quotes else 0
            code[index] = True
            continue
        if (ending := _html_block(content, previous == "paragraph")) is not None:
            raw_html[index] = True
            if ending == _ENDS_ON_ITS_OWN_LINE:
                previous = "other"
            else:
                html_until = ending
            continue
        if _atx_heading(line):
            previous = "other"
        elif _SETEXT_UNDERLINE.match(line) and (previous == "paragraph" or "-" in line):
            previous = "other"  # a setext underline or a thematic break
        else:
            previous = "paragraph"
    return code, raw_html


def _mask_code_spans_and_comments(text: str) -> str:
    """Blank inline code spans and HTML comments, reading left to right.

    Whichever starts first wins, so a ``<!--`` inside a code span opens no
    comment and a backtick inside a comment opens no span. A span closes on
    the next backtick run of the same length in the same block; without one,
    its backticks are literal. A block ends at a blank line, and at a line
    that starts a heading, a list item or a table row; a span opened on a
    heading or table row line ends with that line. A backslash-escaped
    backtick opens nothing.
    """

    out = list(text)

    def blank(start: int, end: int) -> None:
        for position in range(start, end):
            if out[position] != "\n":
                out[position] = " "

    lines = text.split("\n")
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    # Where the block holding each line ends: the start of the next blank or
    # block-starting line, or the end of the text.
    block_end = [len(text)] * (len(lines) + 1)
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not line.strip() or _BLOCK_START.match(line):
            block_end[index] = starts[index]
        else:
            block_end[index] = block_end[index + 1]

    position = 0
    while match := _INLINE_CODE_OR_COMMENT.search(text, position):
        start, end = match.span()
        token = match.group(0)
        if token.startswith("\\"):
            position = end
        elif token == "<!--":
            close = text.find("-->", start + 2)  # <!--> and <!---> close themselves
            if close == -1:
                position = end
            else:
                blank(start, close + 3)
                position = close + 3
        else:
            index = bisect_right(starts, start) - 1
            if _HEADING_OR_TABLE_ROW.match(lines[index]):
                limit = starts[index] + len(lines[index])
            else:
                limit = block_end[index + 1]
            closer = _backtick_run(len(token)).search(text, end, limit)
            if closer is None:
                position = end
            else:
                blank(start, closer.end())
                position = closer.end()
    return "".join(out)


@cache
def _backtick_run(width: int) -> re.Pattern[str]:
    """A run of exactly ``width`` backticks."""

    return re.compile(rf"(?<!`)`{{{width}}}(?!`)")


def renderable_views(text: str) -> tuple[str, str]:
    """``(markdown, html)``: the text GitHub reads as Markdown, and as HTML.

    Both blank front matter, fenced code, code spans and HTML comments to
    spaces, so offsets and line numbers still point into the original file.
    The Markdown view also blanks raw HTML blocks, whose Markdown GitHub does
    not render; the HTML view keeps them, since their tags' attributes are.
    """

    lines = text.split("\n")
    code, raw_html = _block_masks(lines)
    markdown = _mask_code_spans_and_comments(
        "\n".join(_blank(line) if code[i] or raw_html[i] else line for i, line in enumerate(lines))
    )
    markdown_lines = markdown.split("\n")
    html_text = "\n".join(
        line if raw_html[i] else markdown_lines[i] for i, line in enumerate(lines)
    )
    return markdown, _HTML_COMMENT.sub(lambda match: _blank(match.group(0)), html_text)


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------


def heading_text(markdown: str) -> str:
    """The text GitHub renders for a heading's inline Markdown.

    Code spans keep their content, links keep their text, images and HTML
    tags render none, entities and backslash escapes are decoded, and ``_``
    emphasis markers go. An emoji shortcode such as ``:tada:`` stays as typed:
    GitHub slugs the text before it swaps in the emoji. ``*`` and ``~``
    markers need no handling, since the slug drops them. The text is not
    trimmed again: a space before a trailing image or tag stays and becomes a
    trailing ``-``, as it does on GitHub.
    """

    parts: list[str] = []
    position = 0
    for match in re.finditer(r"(`+)(.+?)(?<!`)\1(?!`)", markdown):
        parts.append(_heading_prose(markdown[position : match.start()]))
        content = match.group(2)
        if content.startswith(" ") and content.endswith(" ") and content.strip():
            content = content[1:-1]
        parts.append(content)
        position = match.end()
    parts.append(_heading_prose(markdown[position:]))
    return "".join(parts)


_ESCAPED_UNDERSCORE = "\ue000"  # a private-use character no heading holds


def _heading_prose(markdown: str) -> str:
    # An escaped "\_" is a literal underscore, never an emphasis marker.
    text = markdown.replace("\\_", _ESCAPED_UNDERSCORE)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\](?:\([^)]*\)|\[[^\]]*\])", r"\1", text)
    text = _HTML_COMMENT.sub("", text)
    text = _AUTOLINK.sub(r"\1", text)  # <https://x.y> renders its URL as text
    text = _HTML_TAG.sub("", text)
    text = _UNDERSCORE_EMPHASIS.sub(r"\2", text)
    text = _BACKSLASH_ESCAPE.sub(r"\1", text)
    return html.unescape(text).replace(_ESCAPED_UNDERSCORE, "_")


def github_heading_slug(text: str) -> str:
    """The anchor GitHub derives from a heading's rendered text.

    GitHub lowercases the text, deletes every character that is not a letter,
    mark, decimal digit, connector punctuation (``_``), hyphen or space, and
    turns each space into a hyphen. So ``·``, ``—``, ``.``, ``/``, ``²``,
    backticks and emoji vanish, and the spaces either side of a deleted
    ``—`` leave ``--``.
    """

    kept = (ch for ch in text.lower() if ch in "- " or unicodedata.category(ch) in _SLUG_CATEGORIES)
    return "".join(kept).replace(" ", "-")


def _atx_heading(line: str) -> re.Match[str] | None:
    """An ATX heading, also one inside a blockquote or list item (``> ## A``)."""

    while True:
        match = _ATX_HEADING.match(line)
        if match:
            return match
        marker = _CONTAINER_MARKER.match(line)
        if marker is None or not marker.group(0):
            return None
        line = line[marker.end() :]


def _headings(text: str) -> Iterator[str]:
    """The rendered text of every ATX and setext heading outside code and raw HTML.

    A setext heading's text is its whole paragraph; GitHub joins the lines
    with a newline, which the slug then drops.
    """

    lines = text.split("\n")
    code, raw_html = _block_masks(lines)
    skipped = [is_code or is_html for is_code, is_html in zip(code, raw_html, strict=True)]

    def paragraph_line(index: int) -> bool:
        line = lines[index]
        return (
            bool(line.strip())
            and not skipped[index]
            and not _NOT_A_PARAGRAPH.match(line)
            and _atx_heading(line) is None
            and not _SETEXT_UNDERLINE.match(line)
        )

    for index, line in enumerate(lines):
        if skipped[index]:
            continue
        match = _atx_heading(line)
        if match:
            yield heading_text(match.group(2) or "")
            continue
        if index == 0 or not _SETEXT_UNDERLINE.match(line) or not paragraph_line(index - 1):
            continue
        first = index - 1
        while first > 0 and paragraph_line(first - 1):
            first -= 1
        before = lines[first - 1] if first > 0 else ""
        if (
            first == 0
            or not before.strip()
            or skipped[first - 1]
            or _atx_heading(before)
            or _SETEXT_UNDERLINE.match(before)
        ):
            yield "\n".join(heading_text(lines[i].strip()) for i in range(first, index))


def heading_anchors(text: str) -> list[str]:
    """Each heading's anchor, in order, made unique as GitHub makes it.

    A repeat gets the first free ``-1``, ``-2``…, and a suffixed anchor is
    itself taken: ``Same``, ``Same``, ``Same-1`` give ``same``, ``same-1``,
    ``same-1-1``.
    """

    anchors: list[str] = []
    occurrences: dict[str, int] = {}
    for heading in _headings(text):
        base = slug = github_heading_slug(heading)
        while slug in occurrences:
            occurrences[base] += 1
            slug = f"{base}-{occurrences[base]}"
        occurrences[slug] = 0
        anchors.append(slug)
    return anchors


def anchors_in(text: str) -> frozenset[str]:
    """Every fragment that lands on a heading or explicit anchor in ``text``."""

    explicit = (
        next(group for group in match.groups() if group is not None)
        for match in _HTML_ANCHOR_ATTRIBUTE.finditer(renderable_views(text)[1])
    )
    return frozenset(heading_anchors(text)).union(explicit)


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def _inline_destination(text: str, start: int) -> str | None:
    """The destination of an inline link whose ``(`` ends at ``start``.

    Returns ``None`` when what follows is not a link destination with an
    optional title and a closing ``)``, as CommonMark requires.
    """

    position = start
    while position < len(text) and text[position] in " \t":
        position += 1
    if position < len(text) and text[position] == "\n":
        position += 1
        while position < len(text) and text[position] in " \t":
            position += 1
    if position < len(text) and text[position] == "<":
        end = text.find(">", position)
        if end == -1 or "\n" in text[position:end]:
            return None
        destination = text[position + 1 : end]
        position = end + 1
    else:
        depth = 0
        begin = position
        while position < len(text):
            ch = text[position]
            if ch == "\\" and position + 1 < len(text):
                position += 2
                continue
            if ch.isspace() or ord(ch) < 0x20:
                break
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            position += 1
        destination = text[begin:position]
    if _LINK_TITLE_AND_CLOSE.match(text, position) is None:
        return None
    return _decode_destination(destination)


def _decode_destination(destination: str) -> str:
    """Backslash escapes and entity references, which a destination honours."""

    destination = _BACKSLASH_ESCAPE.sub(r"\1", destination)
    return _ENTITY.sub(lambda match: html.unescape(match.group(0)), destination)


def _escaped(text: str, position: int) -> bool:
    """Whether an odd run of backslashes precedes ``text[position]``."""

    backslashes = 0
    while position - backslashes > 0 and text[position - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _link_text_start(text: str, close: int) -> int | None:
    """The ``[`` that the ``]`` at ``close`` closes, within its block.

    Brackets nest to any depth, and an escaped bracket does not count, so
    ``[a [b [c]]](x)`` is a link and ``\\[x](y)`` is not. The search stops at a
    blank line and at the start of a line that begins a block.
    """

    depth = 1
    position = close - 1
    while position >= 0:
        ch = text[position]
        if ch == "\n":
            previous = text.rfind("\n", 0, position)
            if not text[previous + 1 : position].strip() or _BLOCK_START.match(text, position + 1):
                return None
        elif ch in "[]" and not _escaped(text, position):
            depth += 1 if ch == "]" else -1
            if depth == 0:
                return position
        position -= 1
    return None


def links_in(text: str) -> Iterator[tuple[int, str]]:
    """``(line, destination)`` for every link GitHub would render in ``text``."""

    markdown, html_view = renderable_views(text)

    def line_of(offset: int) -> int:
        return markdown.count("\n", 0, offset) + 1

    for match in re.finditer(r"\]\(", markdown):
        if _escaped(markdown, match.start()):
            continue
        start = _link_text_start(markdown, match.start())
        if start is None:
            continue
        destination = _inline_destination(markdown, match.end())
        if destination is not None:
            yield line_of(start), destination
    for match in _REFERENCE_DEFINITION.finditer(markdown):
        yield line_of(match.start()), _decode_destination(match.group(1).strip("<>"))
    for match in _HTML_LINK_ATTRIBUTE.finditer(html_view):
        value = next(group for group in match.groups() if group is not None)
        yield line_of(match.start()), html.unescape(value)


# ---------------------------------------------------------------------------
# The repository
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Repository:
    """The tracked paths of a checkout, read from its working tree."""

    root: Path
    files: frozenset[str]
    directories: frozenset[str]
    _anchors: dict[str, frozenset[str] | None] = field(
        default_factory=dict, compare=False, hash=False, repr=False
    )
    _folded: dict[str, str] = field(default_factory=dict, compare=False, hash=False, repr=False)

    @classmethod
    def from_paths(cls, root: Path, files: Iterable[str]) -> Repository:
        tracked = frozenset(files)
        directories = {""}
        for path in tracked:
            parent = posixpath.dirname(path)
            while parent and parent not in directories:
                directories.add(parent)
                parent = posixpath.dirname(parent)
        return cls(root=root, files=tracked, directories=frozenset(directories))

    @classmethod
    def from_git(cls, root: Path) -> Repository:
        listed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return cls.from_paths(root, (path for path in listed.split("\0") if path))

    def markdown_files(self) -> list[str]:
        return sorted(path for path in self.files if path.lower().endswith(MARKDOWN_SUFFIXES))

    def read(self, path: str) -> str | None:
        try:
            return (self.root / path).read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError):
            return None

    def anchors(self, path: str) -> frozenset[str] | None:
        if path not in self._anchors:
            text = self.read(path)
            self._anchors[path] = None if text is None else anchors_in(text)
        return self._anchors[path]

    def tracked_spelling(self, path: str) -> str | None:
        """The tracked path that differs from ``path`` only in case, if any."""

        if not self._folded:
            for tracked in sorted(self.files | self.directories):
                self._folded.setdefault(tracked.casefold(), tracked)
        return self._folded.get(path.casefold())


def is_external(destination: str) -> bool:
    return destination.startswith("//") or bool(_URL_SCHEME.match(destination))


def relative_links(repository: Repository) -> Iterator[tuple[str, int, str]]:
    """``(source, line, destination)`` for every in-repository link."""

    for source in repository.markdown_files():
        text = repository.read(source)
        if text is None:
            continue
        for line, destination in links_in(text):
            destination = destination.strip()
            if destination and not is_external(destination):
                yield source, line, destination


def resolve(repository: Repository, source: str, destination: str) -> str | None:
    """Why a relative ``destination`` linked from ``source`` is broken, or ``None``."""

    path, _, fragment = destination.partition("#")
    path = unquote(path.split("?", 1)[0])
    fragment = unquote(fragment)

    target = source
    if path:
        if path.startswith("/"):
            joined = path.lstrip("/")
        else:
            joined = posixpath.join(posixpath.dirname(source), path)
        target = posixpath.normpath(joined) if joined else "."
        if target == ".":
            target = ""
        if target == ".." or target.startswith("../"):
            return "leaves the repository"
        if target not in repository.files and target not in repository.directories:
            # GitHub matches case exactly; a case-insensitive disk does not.
            if (spelling := repository.tracked_spelling(target)) is not None:
                return f"{target} differs in case from tracked {spelling}"
            if (repository.root / target).exists():
                return f"{target} exists but is not tracked by git"
            return f"{target} does not exist"

    if not fragment:
        return None
    if target in repository.directories:
        # GitHub shows a directory's README.md below its listing.
        readme = posixpath.join(target, "README.md")
        if readme not in repository.files:
            return f"#{fragment}: directory {target or '.'} has no README.md to hold it"
        target = readme
    if not target.lower().endswith(MARKDOWN_SUFFIXES):
        return None
    anchors = repository.anchors(target)
    if anchors is None:
        return f"{target} is tracked but missing from the working tree"
    if fragment in anchors or fragment.removeprefix("user-content-") in anchors:
        return None
    return f"#{fragment} is not a heading or anchor in {target}"


def broken_links(
    repository: Repository, links: Iterable[tuple[str, int, str]] | None = None
) -> list[BrokenLink]:
    return [
        BrokenLink(source, line, destination, reason)
        for source, line, destination in (relative_links(repository) if links is None else links)
        if (reason := resolve(repository, source, destination)) is not None
    ]


@lru_cache(maxsize=1)
def _this_repository() -> Repository:
    return Repository.from_git(REPO_ROOT)


@lru_cache(maxsize=1)
def _this_repository_links() -> tuple[tuple[str, int, str], ...]:
    return tuple(relative_links(_this_repository()))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_relative_link_and_anchor_in_tracked_markdown_resolves() -> None:
    broken = broken_links(_this_repository(), _this_repository_links())
    assert not broken, (
        f"{len(broken)} broken relative link(s) or anchor(s) in tracked Markdown. "
        "Point each at a tracked file and at a heading or <a id> it has. A file "
        "under docs/changelog/ is a release record moved from CHANGELOG.md: its "
        "repository links need ../../ in front.\n  " + "\n  ".join(str(link) for link in broken)
    )


def test_the_check_reads_the_repository_and_is_not_vacuous() -> None:
    """A parser that found no links would pass the test above with nothing read."""

    repository = _this_repository()
    links = _this_repository_links()
    sources = {source for source, _, _ in links}
    fragments = [destination for _, _, destination in links if "#" in destination]
    # 354 files, 187 of them with 2,169 relative links, 430 with a fragment,
    # when this was written.
    assert len(repository.markdown_files()) > 300
    assert len(sources) > 150
    assert len(links) > 1500
    assert len(fragments) > 300


def test_a_planted_broken_link_and_anchor_are_reported(tmp_path: Path) -> None:
    files = {
        "README.md": (
            "# Project\n\n## Install (canonical)\n\n"
            '<a id="pinned"></a>\n\nSame file: [top](#project).\n'
        ),
        "docs/guide.md": (
            "# Guide\n\n"
            "[file](../README.md)\n"
            "[heading](../README.md#install-canonical)\n"
            "[explicit anchor](../README.md#pinned)\n"
            "[root-relative](/README.md#project)\n"
            "[directory](../samples/)\n"
            "[directory README anchor](../pkg/#pkg)\n"
            "[prefixed anchor](../README.md#user-content-project)\n"
            "[line anchor](../src/app.py#L10)\n"
            '[titled](../README.md "Project")\n'
            "[self](#guide)\n"
            "[external](https://example.com/missing.md)\n"
            "`[code span](missing-in-code.md)`\n\n"
            "```text\n[fenced](missing-in-fence.md)\n```\n\n"
            "<!-- [comment](missing-in-comment.md) -->\n\n"
            "[broken anchor](../README.md#installation)\n"
            "[missing file](missing.md)\n"
            "[moved release record](README.md#project)\n"
            "[untracked](notes.md)\n"
            "[outside](../../outside.md)\n"
            "[self, broken](#nowhere)\n"
            "[no README](../samples/#x)\n"
            "[wrong case](../readme.md)\n"
            '<a href="missing-html.md">html</a>\n\n'
            "[ref]: missing-ref.md\n"
        ),
        "docs/notes.md": "# Written, never added\n",
        "pkg/README.md": "# Pkg\n",
        "samples/data.json": "{}\n",
        "src/app.py": "pass\n",
    }
    for relative, content in files.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(content, encoding="utf-8")
    tracked = set(files) - {"docs/notes.md"}

    broken = broken_links(Repository.from_paths(tmp_path, tracked))

    assert {link.target: link.reason for link in broken} == {
        "../README.md#installation": "#installation is not a heading or anchor in README.md",
        "missing.md": "docs/missing.md does not exist",
        "README.md#project": "docs/README.md does not exist",
        "notes.md": "docs/notes.md exists but is not tracked by git",
        "../../outside.md": "leaves the repository",
        "#nowhere": "#nowhere is not a heading or anchor in docs/guide.md",
        "../samples/#x": "#x: directory samples has no README.md to hold it",
        "../readme.md": "readme.md differs in case from tracked README.md",
        "missing-html.md": "docs/missing-html.md does not exist",
        "missing-ref.md": "docs/missing-ref.md does not exist",
    }
    guide = files["docs/guide.md"].split("\n")
    anchor = next(link for link in broken if link.target == "../README.md#installation")
    assert anchor.source == "docs/guide.md"
    assert guide[anchor.line - 1] == "[broken anchor](../README.md#installation)"


def test_code_and_comments_hide_links_and_nothing_else() -> None:
    """GitHub renders exactly these destinations from this text (2026-11-27).

    An unmatched backtick must not hide what follows it beyond its own block:
    across list items, table rows or a heading line, a stray backtick on each
    side once read every link between them as code, so a broken one passed.
    """

    text = (
        "Open `<!--` in code, then [after code](a.md).\n\n"
        "Text <!-- a `backtick in a comment --> then [after comment](b.md)\n\n"
        "1. Step\n\n   - Nested\n\n     ```bash\n     [indented fence](c.md)\n     ```\n\n"
        "\\`not code [escaped tick](d.md)\\`\n\n"
        "``a ` b [in double ticks](e.md) ``\n\n"
        "An unclosed ` tick [still a link](f.md)\n\n"
        "![image](g.png) and [ref][r]\n\n[r]: h.md\n\n"
        "- Use the ` character\n- See [doc](cross-list.md) and ` here\n\n"
        "| a ` | b |\n| --- | --- |\n| [t](cross-table.md) | ` c |\n\n"
        "Para with ` tick\n## Heading [h](cross-heading.md) `\nmore\n\n"
        "## Second ` [h2](heading-line.md)\nnext ` line\n\n"
        "```\ncode\n    ```\n[in fence](after-indented-closer.md)\n```\n\n"
        "\\[escaped](escaped-bracket.md)\n"
        "\n> ~~~md\n> [tilde fence](quoted-tilde.md)\n> ~~~\n\n"
        "> > ```md\n> > [unclosed fence](unclosed-quote.md)\n"
        "> [outer quote](outer-quote.md)\n\n"
    )
    assert [destination for _, destination in links_in(text)] == [
        "a.md",
        "b.md",
        "d.md",
        "f.md",
        "g.png",
        "cross-list.md",
        "cross-table.md",
        "cross-heading.md",
        "heading-line.md",
        "outer-quote.md",
        "h.md",
    ]


def test_raw_html_blocks_render_attributes_and_no_markdown() -> None:
    """GitHub's reading of raw HTML blocks (2026-11-27).

    Inside one, a Markdown link is text and a fence opens nothing, so the
    heading after it is a heading; ``examples/golden-prs`` has that shape.
    """

    text = (
        "<details>\n<summary>Raw</summary>\n"
        '[raw](raw-block.md) and <a href="html-href.md">a</a>\n</details>\n```\n\n'
        "## After the raw block\n\n[after](after-raw.md)\n\n"
        "<!--\n```\n-->\n[after comment block](after-comment-block.md)\n"
    )
    assert [destination for _, destination in links_in(text)] == [
        "after-raw.md",
        "after-comment-block.md",
        "html-href.md",
    ]
    assert heading_anchors(text) == ["after-the-raw-block"]


def test_html_block_starts_and_indented_code_match_github() -> None:
    """Which lines open a raw HTML block or indented code, as GitHub reads them (2026-11-27).

    A start indented four columns is code, not HTML; ``<!-->`` is a whole
    comment; ``<source>`` is a block-level tag and ``<search>`` is not; a lone
    tag opens a block after a heading or fence but not inside a paragraph,
    and only if its attributes are well formed. Each wrong reading hid every
    link after it, up to the end of the file or the next blank line.
    """

    text = (
        "# Doc\n\nThe marker looks like this:\n\n    <!-- agents-shipgate:begin\n\n"
        "## Later section\n\n[later](later.md)\n\n"
        "<!-->\n[after empty comment](after-empty-comment.md)\n\n"
        "<!--->\n[after dash comment](after-dash-comment.md)\n\n"
        "Inline <!--> then [inline after](inline-after.md)\n\n"
        "<search> [search](search-link.md)\n\n"
        '<source media="print"> [source](source-link.md)\n\n'
        "Para\n<span>\n[after para span](after-para-span.md)\n\n"
        "## Heading\n<span>\n[after heading span](after-heading-span.md)\n\n"
        "```\nx\n```\n<span>\n[after fence span](after-fence-span.md)\n\n"
        '<x "y">\n[after bad attr](after-bad-attr.md)\n\n'
        "<foo bar=>\n[after bad attr 2](after-bad-attr-2.md)\n\n"
        "<?php echo 1; ?>\n[after php](after-php.md)\n\n"
        "Para two\n    <div>\n[after indented div](after-indented-div.md)\n\n"
        "\t<span>\n[after tab span](after-tab-span.md)\n\n"
        "- item\n\n      ```\n      [indented code in list](in-list-code.md)\n      ```\n\n"
        "[end](end.md)\n"
    )
    assert [destination for _, destination in links_in(text)] == [
        "later.md",
        "after-empty-comment.md",
        "after-dash-comment.md",
        "inline-after.md",
        "search-link.md",
        "after-para-span.md",
        "after-bad-attr.md",
        "after-bad-attr-2.md",
        "after-php.md",
        "after-indented-div.md",
        "after-tab-span.md",
        "end.md",
    ]
    assert heading_anchors(text) == ["doc", "later-section", "heading"]


def test_list_items_comments_and_processing_blocks_match_github() -> None:
    """More of GitHub's reading (2026-11-27): a ``<?`` block runs to ``?>``,
    ``<!-->`` closes itself inline and inside raw HTML, and a line indented
    four columns inside a list item, even after a lazy continuation line, is
    the item's text rather than code.
    """

    text = (
        "<?php\n[in php](in-php.md)\n?>\n[after php block](after-php-block.md)\n\n"
        "Inline <!--> then [inline after](inline-after.md) and --> end\n\n"
        '<div>\n<!--> <a href="kept.md">k</a> -->\n</div>\n\n'
        "- item\nlazy continuation\n\n    [in item](in-item.md)\n\n"
        "- other item\n\n    [in other item](in-other-item.md)\n"
    )
    assert [destination for _, destination in links_in(text)] == [
        "after-php-block.md",
        "inline-after.md",
        "in-item.md",
        "in-other-item.md",
        "kept.md",
    ]


def test_fences_and_code_after_list_and_quote_markers_match_github() -> None:
    """GitHub's reading of these (2026-11-27).

    A fence on the same line as a list or quote marker (numbered install
    steps write ``2. ```bash``) is a fence; missing it read its closing line
    as an opener and hid the rest of the file. A tab after a list marker
    counts from the marker's column; ``>     ```` is indented code in a quote;
    an unindented paragraph after a blank line closes a list item; a ``===``
    line after a blank line is a paragraph.
    """

    text = (
        "2. ```bash\n   pip install x\n   ```\n\n## Usage\n\nSee [guide](missing-guide.md).\n\n"
        "- ```\n  [in list fence](in-list-fence.md)\n  ```\n\n[after list fence](after-list-fence.md)\n\n"
        "> - ```\n>   [in quoted list fence](in-quoted-list-fence.md)\n>   ```\n\n"
        "[after quoted list fence](after-quoted-list-fence.md)\n\n"
        "- > ```\n  > [in list quote fence](in-list-quote-fence.md)\n  > ```\n\n"
        "[after list quote fence](after-list-quote-fence.md)\n\n"
        "-\tfoo\n\n    [tab item](tabitem.md)\n\n"
        "1.\tfoo\n\n    [tab ordered](tabordered.md)\n\n"
        ">     ```\n>     [quote indented code](quote-indented-code.md)\n"
        "> [after quote code](after-quote-code.md)\n\n"
        "- item\n\nParagraph\n\n    [lp code](lp-code.md)\n\n"
        "Para\n\n===\n    [eq](eq-para.md)\n\n## End\n\n[end](end.md)\n"
    )
    assert [destination for _, destination in links_in(text)] == [
        "missing-guide.md",
        "after-list-fence.md",
        "after-quoted-list-fence.md",
        "after-list-quote-fence.md",
        "tabitem.md",
        "tabordered.md",
        "after-quote-code.md",
        "eq-para.md",
        "end.md",
    ]
    assert heading_anchors(text) == ["usage", "end"]

    # An unclosed fence ends with its list item, even inside a quote.
    for sample in (
        "- ~~~\n  [example](in-fence.md)\n\n## After\n[link](after.md)\n",
        "> - ~~~\n>   > [example](in-fence.md)\n>\n> ## After\n> [link](after.md)\n",
        "- > ~~~\n  > [example](in-fence.md)\n> ## After\n> [link](after.md)\n",
    ):
        assert [destination for _, destination in links_in(sample)] == ["after.md"]
        assert heading_anchors(sample) == ["after"]


def test_nested_list_items_indented_four_columns_keep_their_paragraphs() -> None:
    """GitHub's reading of nested lists indented four columns (2026-11-27).

    ``    - b`` under ``- a`` (the MkDocs style) is a nested item, because its
    indentation counts from the parent item's content column. Counted from
    column 0 it was no item, and the paragraph or fence under it read as
    indented code, which hid its links.
    """

    text = (
        "- a\n    - b\n\n      See [x](p1.md)\n\nBreak one.\n\n"
        "- a\n    - b\n\n      ```bash\n      run\n      ```\n\n      after [x](p5.md)\n\n"
        "Break two.\n\n"
        "- a\n  - b\n    - c\n\n        [x](p7.md)\n\nBreak three.\n\n"
        "* one\n    * two\n        * three\n\n            [deep](deep.md)\n"
    )
    assert [destination for _, destination in links_in(text)] == [
        "p1.md",
        "p5.md",
        "p7.md",
        "deep.md",
    ]


def test_a_start_in_indented_code_hides_no_later_link_or_heading(tmp_path: Path) -> None:
    """The review's repro: an unclosed ``<!--`` in an indented code block."""

    files = {
        "a.md": (
            "# Doc\n\nThe marker looks like this:\n\n    <!-- agents-shipgate:begin\n\n"
            "## Later section\n\n[broken](missing-1.md)\n\n[also broken](#nowhere)\n\n"
            "More prose [x](missing-2.md).\n"
        ),
        "b.md": "[later](a.md#later-section)\n",
    }
    for relative, content in files.items():
        (tmp_path / relative).write_text(content, encoding="utf-8")

    broken = broken_links(Repository.from_paths(tmp_path, files))

    assert [(link.source, link.target) for link in broken] == [
        ("a.md", "missing-1.md"),
        ("a.md", "#nowhere"),
        ("a.md", "missing-2.md"),
    ]


def test_link_syntax_github_reads_and_the_parser_must_too() -> None:
    """GitHub renders each of these as a link to the file named (2026-11-27)."""

    text = (
        "[a [b [c]]](nested.md)\n\n"
        '<A HREF="upper.md">x</A>\n\n'
        "[x](a&amp;b.md)\n\n"
        "Use [r] here.\n\n[r]:\n  next-line.md\n"
    )
    assert [destination for _, destination in links_in(text)] == [
        "nested.md",
        "a&b.md",
        "next-line.md",
        "upper.md",
    ]


# Each expected anchor is what GitHub's renderer produced for the heading
# (`gh api markdown -f mode=markdown`, 2026-11-27). The first group are real
# headings that links in this repository target; the rest pin rules the
# repository's headings do not exercise yet. The same run matched all 1,951
# distinct headings in the repository's tracked Markdown.
@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        # STABILITY.md: code spans, dots and underscores.
        (
            "`release_decision.decision` vs `summary.status`",
            "release_decisiondecision-vs-summarystatus",
        ),
        # docs/agent-recipes.md: a middle dot leaves its two spaces.
        (
            "Recipe 2 · Add Shipgate to a repo that already has tool surfaces",
            "recipe-2--add-shipgate-to-a-repo-that-already-has-tool-surfaces",
        ),
        # docs/quickstart.md: an em dash.
        ("Route H — no manifest", "route-h--no-manifest"),
        # docs/release-evidence-policy-decision.md: `*` inside a code span.
        (
            "Amendment 5 — the advisory line may publish a `v*` release",
            "amendment-5--the-advisory-line-may-publish-a-v-release",
        ),
        # docs/release-runbook.md: a comma and an apostrophe.
        ("The environment is locked, and it is CI's", "the-environment-is-locked-and-it-is-cis"),
        # docs/host-boundary-support.md: a code span with a leading dot.
        ("Which host a `.mcp.json` belongs to", "which-host-a-mcpjson-belongs-to"),
        # docs/upstream-integrations.md: parentheses and a plus.
        ("Google ADK (Python + Agent Config YAML)", "google-adk-python--agent-config-yaml"),
        # STABILITY.md's title, which every release re-stamps.
        ("Stability Contract · 1.3.0", "stability-contract--130"),
        # Rules the repository's headings do not exercise yet.
        ("🚀 Launch :tada: now", "-launch-tada-now"),
        ('A &amp; B <code>x.y</code> <a id="foo"></a>', "a--b-xy-"),
        ("[Link text](https://x.y) and ![img](a.png)", "link-text-and-"),
        ("_emph_ and snake_case and __bold__", "emph-and-snake_case-and-bold"),
        ("_private method", "_private-method"),
        ("__init__ method", "init-method"),
        ("x_ y _z", "x_-y-_z"),
        ("a\\_b \\* c", "a_b--c"),
        ("*star* and **bold** ~~strike~~", "star-and-bold-strike"),
        ("Foo² bar½", "foo-bar"),
        ("Café naïve Ünïcode", "café-naïve-ünïcode"),
        ("Two  spaces", "two--spaces"),
        ("Trailing hashes ##", "trailing-hashes"),
        ("Title <!-- note -->", "title-"),
        ("See <https://x.y>", "see-httpsxy"),
        ("Heading [h](x.md) `", "heading-h-"),
    ],
)
def test_heading_anchor_matches_github(heading: str, anchor: str) -> None:
    assert heading_anchors(f"## {heading}\n") == [anchor]


def test_repeated_headings_take_the_first_free_suffix() -> None:
    text = "## Same\n\n## Same\n\n## Same\n\n## Same-1\n\n```\n## Same\n```\n\nSame\n----\n"
    assert heading_anchors(text) == ["same", "same-1", "same-2", "same-1-1", "same-3"]


def test_headings_in_containers_and_multi_line_setext_headings_get_anchors() -> None:
    """GitHub's anchors for these headings (2026-11-27)."""

    text = (
        "> ## Quoted\n\n## Quoted\n\n"
        "Line one\nLine two\n========\n\n"
        "- ## In list\n\n1. ## In ordered list\n"
    )
    assert heading_anchors(text) == [
        "quoted",
        "quoted-1",
        "line-oneline-two",
        "in-list",
        "in-ordered-list",
    ]


@pytest.mark.parametrize(
    ("target", "anchor"),
    [
        ("STABILITY.md", "release_decisiondecision-vs-summarystatus"),
        ("STABILITY.md", "json-report-fields-stable"),
        ("STABILITY.md", "migration-note-0-14-0"),
        (
            "docs/agent-recipes.md",
            "recipe-2--add-shipgate-to-a-repo-that-already-has-tool-surfaces",
        ),
        ("docs/quickstart.md", "route-h--no-manifest"),
        ("docs/release-runbook.md", "the-environment-is-locked-and-it-is-cis"),
        ("docs/determinism-boundary.md", "codex--mcp-host-config"),
        ("docs/manifest-v0.1.md", "openai-api-artifacts"),
        ("samples/google_adk_cold_start_agent/README.md", "regenerating-the-goldens"),
    ],
)
def test_known_anchors_with_tricky_headings_resolve(target: str, anchor: str) -> None:
    """Anchors that links in this repository use, read from the files themselves."""

    assert resolve(_this_repository(), "README.md", f"{target}#{anchor}") is None
