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
  Fenced code blocks, inline code spans, HTML comments and YAML front matter
  are not rendered as links, so they are skipped. A target with a URL scheme
  (``https:``, ``mailto:``) or ``//`` is external and not fetched.
* **Targets** resolve the way GitHub resolves them: relative to the linking
  file's directory, or to the repository root when they begin with ``/``. A
  target must be a tracked file, or a directory holding one. A path that
  exists on disk but is untracked or ignored is reported as broken: the link
  is dead on GitHub and in every other clone, so ``git add`` it or drop it.
  A target that leaves the repository is broken too.
* **Anchors** in a ``.md`` target are checked against the ids GitHub gives
  that file: each heading's slug (see :func:`github_heading_slug`) with
  ``-1``, ``-2`` for repeats, and every explicit ``id=``/``name=`` attribute.
  A fragment on any other file (a ``#L12`` line anchor on a ``.py`` file) is
  not checked.

The parser is a reading of GitHub's renderer, not a Markdown implementation,
so it was checked against GitHub (``gh api markdown -f mode=markdown``) when
it was written: for each of the 354 tracked Markdown files, the relative link
destinations it extracts equal those GitHub renders, and the anchors it gives
all 1,951 distinct headings equal GitHub's. The tests below pin the cases
that check covered.
"""

from __future__ import annotations

import html
import posixpath
import re
import subprocess
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

MARKDOWN_SUFFIXES = (".md", ".markdown")

_FENCE_OPEN = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_NOT_A_PARAGRAPH = re.compile(r"^ {0,3}(?:[#>|]|[-+*][ \t]|\d+[.)][ \t]|$)")
_INLINE_CODE_OR_COMMENT = re.compile(r"\\[\\`<]|`+|<!--")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")
_HTML_TAG = re.compile(r"<[A-Za-z][^<>]*>|</[A-Za-z][^<>]*>")
_HTML_ANCHOR_ATTRIBUTE = re.compile(
    r"<[A-Za-z][^<>]*?\s(?:id|name)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))",
)
_HTML_LINK_ATTRIBUTE = re.compile(
    r"<[A-Za-z][^<>]*?\s(?:href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))",
)
# Link text may hold one level of nested brackets: [`a[0]`](x), [![img](i)](x).
_INLINE_LINK_OPEN = re.compile(r"!?\[(?:[^\[\]\\]|\\.|\[(?:[^\[\]\\]|\\.)*\])*\]\(")
_REFERENCE_DEFINITION = re.compile(
    r"^[ \t>]*\[(?!\^)(?:[^\[\]\\]|\\.)+\]:[ \t]*(<[^<>\n]*>|\S+)", re.MULTILINE
)
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


def _code_fence_mask(lines: list[str]) -> list[bool]:
    """``True`` for every line that front matter or a fenced code block holds.

    A fence closes on a line of the same character at least as long, as
    CommonMark says; an unclosed fence runs to the end of the file. A fence
    may be indented, since one inside a list item is.
    """

    masked = [False] * len(lines)
    for index in range(_front_matter_end(lines)):
        masked[index] = True
    open_fence: str | None = None
    for index, line in enumerate(lines):
        if masked[index]:
            continue
        if open_fence is not None:
            masked[index] = True
            stripped = line.strip()
            if stripped and set(stripped) == {open_fence[0]} and len(stripped) >= len(open_fence):
                open_fence = None
            continue
        match = _FENCE_OPEN.match(line)
        if match and not (match.group("fence")[0] == "`" and "`" in match.group("info")):
            open_fence = match.group("fence")
            masked[index] = True
    return masked


def _mask_code_spans_and_comments(text: str) -> str:
    """Blank inline code spans and HTML comments, reading left to right.

    Whichever starts first wins, so a ``<!--`` inside a code span opens no
    comment and a backtick inside a comment opens no span. A span closes on
    the next backtick run of the same length in the same paragraph; without
    one, its backticks are literal. A backslash-escaped backtick opens nothing.
    """

    out = list(text)

    def blank(start: int, end: int) -> None:
        for position in range(start, end):
            if out[position] != "\n":
                out[position] = " "

    position = 0
    while match := _INLINE_CODE_OR_COMMENT.search(text, position):
        start, end = match.span()
        token = match.group(0)
        if token.startswith("\\"):
            position = end
        elif token == "<!--":
            close = text.find("-->", end)
            if close == -1:
                position = end
            else:
                blank(start, close + 3)
                position = close + 3
        else:
            paragraph_end = _BLANK_LINE.search(text, end)
            limit = paragraph_end.start() if paragraph_end else len(text)
            closer = re.compile(rf"(?<!`)`{{{len(token)}}}(?!`)").search(text, end, limit)
            if closer is None:
                position = end
            else:
                blank(start, closer.end())
                position = closer.end()
    return "".join(out)


def renderable_text(text: str) -> str:
    """The Markdown with everything GitHub does not render as a link blanked.

    Front matter, fenced code, code spans and HTML comments become spaces, so
    offsets and line numbers still point into the original file.
    """

    lines = text.split("\n")
    fenced = _code_fence_mask(lines)
    kept = "\n".join(_blank(line) if fenced[i] else line for i, line in enumerate(lines))
    return _mask_code_spans_and_comments(kept)


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


def _headings(text: str) -> Iterator[str]:
    """The rendered text of every ATX and setext heading outside code."""

    lines = text.split("\n")
    fenced = _code_fence_mask(lines)
    for index, line in enumerate(lines):
        if fenced[index]:
            continue
        match = _ATX_HEADING.match(line)
        if match:
            yield heading_text(match.group(2) or "")
            continue
        if (
            index > 0
            and _SETEXT_UNDERLINE.match(line)
            and not fenced[index - 1]
            and not _NOT_A_PARAGRAPH.match(lines[index - 1])
            and not _ATX_HEADING.match(lines[index - 1])
            and (index < 2 or not lines[index - 2].strip() or fenced[index - 2])
        ):
            yield heading_text(lines[index - 1].strip())


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
        for match in _HTML_ANCHOR_ATTRIBUTE.finditer(renderable_text(text))
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
    rest = text[position:]
    match = re.match(
        r"[ \t]*\n?[ \t]*(?:\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\))?[ \t]*\n?[ \t]*\)",
        rest,
    )
    if match is None:
        return None
    return _BACKSLASH_ESCAPE.sub(r"\1", destination)


def links_in(text: str) -> Iterator[tuple[int, str]]:
    """``(line, destination)`` for every link GitHub would render in ``text``."""

    rendered = renderable_text(text)

    def line_of(offset: int) -> int:
        return rendered.count("\n", 0, offset) + 1

    for match in _INLINE_LINK_OPEN.finditer(rendered):
        destination = _inline_destination(rendered, match.end())
        if destination is not None:
            yield line_of(match.start()), destination
    for match in _REFERENCE_DEFINITION.finditer(rendered):
        yield line_of(match.start()), match.group(1).strip("<>")
    for match in _HTML_LINK_ATTRIBUTE.finditer(rendered):
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


def broken_links(repository: Repository) -> list[BrokenLink]:
    return [
        BrokenLink(source, line, destination, reason)
        for source, line, destination in relative_links(repository)
        if (reason := resolve(repository, source, destination)) is not None
    ]


@lru_cache(maxsize=1)
def _this_repository() -> Repository:
    return Repository.from_git(REPO_ROOT)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_relative_link_and_anchor_in_tracked_markdown_resolves() -> None:
    broken = broken_links(_this_repository())
    assert not broken, (
        f"{len(broken)} broken relative link(s) or anchor(s) in tracked Markdown. "
        "Point each at a tracked file and at a heading or <a id> it has. A file "
        "under docs/changelog/ is a release record moved from CHANGELOG.md: its "
        "repository links need ../../ in front.\n  " + "\n  ".join(str(link) for link in broken)
    )


def test_the_check_reads_the_repository_and_is_not_vacuous() -> None:
    """A parser that found no links would pass the test above with nothing read."""

    repository = _this_repository()
    links = list(relative_links(repository))
    sources = {source for source, _, _ in links}
    fragments = [destination for _, _, destination in links if "#" in destination]
    # 354 files, 187 of them with 2,169 relative links, 429 with a fragment,
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
            '<a href="missing-html.md">html</a>\n\n'
            "[ref]: missing-ref.md\n"
        ),
        "docs/notes.md": "# Written, never added\n",
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
        "missing-html.md": "docs/missing-html.md does not exist",
        "missing-ref.md": "docs/missing-ref.md does not exist",
    }
    guide = files["docs/guide.md"].split("\n")
    anchor = next(link for link in broken if link.target == "../README.md#installation")
    assert anchor.source == "docs/guide.md"
    assert guide[anchor.line - 1] == "[broken anchor](../README.md#installation)"


def test_code_and_comments_hide_links_and_nothing_else() -> None:
    """GitHub renders exactly these destinations from this text (2026-11-27)."""

    text = (
        "Open `<!--` in code, then [after code](a.md).\n\n"
        "Text <!-- a `backtick in a comment --> then [after comment](b.md)\n\n"
        "1. Step\n\n   - Nested\n\n     ```bash\n     [indented fence](c.md)\n     ```\n\n"
        "\\`not code [escaped tick](d.md)\\`\n\n"
        "``a ` b [in double ticks](e.md) ``\n\n"
        "An unclosed ` tick [still a link](f.md)\n\n"
        "![image](g.png) and [ref][r]\n\n[r]: h.md\n"
    )
    assert [destination for _, destination in links_in(text)] == [
        "a.md",
        "b.md",
        "d.md",
        "f.md",
        "g.png",
        "h.md",
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
    ],
)
def test_heading_anchor_matches_github(heading: str, anchor: str) -> None:
    assert heading_anchors(f"## {heading}\n") == [anchor]


def test_repeated_headings_take_the_first_free_suffix() -> None:
    text = "## Same\n\n## Same\n\n## Same\n\n## Same-1\n\n```\n## Same\n```\n\nSame\n----\n"
    assert heading_anchors(text) == ["same", "same-1", "same-2", "same-1-1", "same-3"]


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
