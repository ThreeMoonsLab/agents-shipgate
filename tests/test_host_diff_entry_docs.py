"""#779: the first task the entry pages teach is the output `diff` prints.

The README and the quickstart open on `agents-shipgate diff` against a
permission/MCP change and quote each of its answers. The no-change, not-compared
and cannot-compare quotes were first taken from the published `1.0.0`,
installed outside a checkout and run in a clone of a repository with a remote;
the change quote was this tree's output from #795, and every quote gained the
`What this run established` block with #812, while the pages labelled them as
not yet released. Since #778 every quote is re-captured from the published
`1.1.0`, installed from PyPI into a clean virtualenv outside any checkout, and
the pages say so. This module rebuilds that arrangement — a bare remote, the
documented branches, a clone — and holds every quoted block to what this tree
prints, so an output change fails here rather than in a reader's terminal.
Commit ids and the version on the reference lines are the only normalized
fields.

Neither this tree's output nor a version string can say which build printed a
quote: until its next version bump the tree prints the published version on its
reference lines. So which build a page says its quotes came from is held to a record
taken from the published build itself, `_PUBLISHED_ANSWERS`. A page that calls
its quotes published output may quote only answers in that record, and must
name the newest published release on its label and on every reference line. A
page that calls them not yet released must name that release as the one it
compares them with, and must quote at least one answer that release does not
print.

It also holds the channel statements the entry pages make to the declaration
that decides them, `.github/release-channels.json`, rather than to a second
copy of the answer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.published_release import LATEST_PUBLISHED_VERSION
from scripts.release_channel import load_declaration

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
QUICKSTART = REPO_ROOT / "docs" / "quickstart.md"

_BASE_SETTINGS = """{
  "permissions": {
    "allow": ["Bash(npm test:*)"],
    "deny": ["Bash(rm -rf:*)"]
  }
}
"""
_HEAD_SETTINGS = """{
  "permissions": {
    "allow": ["Bash(npm *)"],
    "deny": []
  }
}
"""
_BASE_MCP = '{"mcpServers": {"docs": {"command": "npx", "args": ["-y", "@example/docs-mcp"]}}}\n'
_HEAD_MCP = (
    '{"mcpServers": {"docs": {"command": "npx", "args": ["-y", "@example/docs-mcp"]}, '
    '"billing": {"command": "npx", "args": ["-y", "@example/billing-mcp"], '
    '"env": {"BILLING_TOKEN": "${BILLING_TOKEN}"}}}}\n'
)
#: Commit ids, abbreviated or whole, and the version a reference line names.
_SHA = re.compile(r"\b(?:[0-9a-f]{40}|[0-9a-f]{8})\b")
_VERSION = re.compile(r"agents-shipgate \d+\.\d+\.\d+\S*?(?=[.,]?(?:\s|$))")
_NO_CHANGE = "No static host-grant changes detected. No verdict is implied."


#: Git that ignores the caller's repository, hooks and global configuration, so a
#: run under `rebase --exec`, a hook, or a customized `~/.gitconfig` builds the
#: same fixture.
_GIT_ENV = {
    **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

#: The CLI's output as a terminal-less reader sees it. Rich colors output under
#: `GITHUB_ACTIONS` and wraps its error panel to `COLUMNS`; neither is part of
#: what the pages quote.
_CLI_ENV = {"NO_COLOR": "1", "GITHUB_ACTIONS": None, "FORCE_COLOR": None, "COLUMNS": "200"}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=Docs", "-c", "user.email=docs@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
         "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=cwd, check=True, capture_output=True, text=True, env=_GIT_ENV,
    )


def _remote(tmp_path: Path, base: dict[str, str], branches: dict[str, dict[str, str]]) -> Path:
    """A bare remote whose `main` holds ``base`` and one branch per entry."""
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    for relative, text in base.items():
        (seed / relative).parent.mkdir(parents=True, exist_ok=True)
        (seed / relative).write_text(text)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    for name, files in branches.items():
        _git(seed, "checkout", "-q", "-B", name, "origin/main")
        for relative, text in files.items():
            (seed / relative).write_text(text)
        _git(seed, "commit", "-qam", name)
        _git(seed, "push", "-q", "origin", name)
    return remote


@pytest.fixture
def documented_remote(tmp_path: Path) -> Path:
    return _remote(
        tmp_path,
        {".claude/settings.json": _BASE_SETTINGS, ".mcp.json": _BASE_MCP, "README.md": "# demo\n"},
        {
            "change": {".claude/settings.json": _HEAD_SETTINGS, ".mcp.json": _HEAD_MCP},
            "docs-only": {"README.md": "# demo\nmore\n"},
            "broken": {".mcp.json": '{"mcpServers": {"docs": '},
        },
    )


def _clone(remote: Path, branch: str, *extra: str) -> Path:
    clone = remote.parent / f"clone-{branch}-{len(extra)}"
    _git(remote.parent, "clone", "-q", *extra, str(remote), str(clone))
    _git(clone, "checkout", "-q", branch)
    return clone


def _diff(repo: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), *args], env=_CLI_ENV)
    # `click` is not in the locked test environment (typer 0.27 dropped it), so
    # strip ANSI escapes the way tests/test_verify_auto_base.py does.
    return result.exit_code, re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.output)


def _flat(output: str) -> str:
    """Error-panel text with the box drawing and wrapping removed."""
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", output).split())


def _normalize(text: str) -> list[str]:
    return [
        _VERSION.sub("agents-shipgate <version>", _SHA.sub("<sha>", line.rstrip()))
        for line in text.strip().splitlines()
    ]


def _blocks(path: Path, first_line_prefix: str) -> list[list[str]]:
    blocks = re.findall(r"```text\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S)
    return [_normalize(block) for block in blocks if block.startswith(first_line_prefix)]


def _quoted(path: Path, kind: str) -> list[list[str]]:
    shapes = {
        "change": lambda block: any("change(s)" in line for line in block),
        "not_compared": lambda block: any(line.startswith("Not compared:") for line in block),
        "no_change": lambda block: _NO_CHANGE in block
        and not any(line.startswith("Not compared:") for line in block),
    }
    return [block for block in _blocks(path, "Agent capability diff") if shapes[kind](block)]


def test_the_quoted_change_rows_are_what_diff_prints(documented_remote: Path) -> None:
    code, output = _diff(_clone(documented_remote, "change"))
    assert code == 0, output
    printed = _normalize(output)
    assert printed[0] == "Agent capability diff  origin/main (<sha>) -> working tree"
    quoted = _quoted(README, "change") + _quoted(QUICKSTART, "change")
    assert len(quoted) == 2, "README.md and docs/quickstart.md each quote the change rows once"
    assert all(block == printed for block in quoted)


def test_the_quoted_no_change_answer_is_what_diff_prints(documented_remote: Path) -> None:
    code, output = _diff(_clone(documented_remote, "docs-only"))
    assert code == 0, output
    assert _quoted(QUICKSTART, "no_change") == [_normalize(output)]


def test_the_quoted_not_compared_answer_is_what_diff_prints(tmp_path: Path) -> None:
    """A no-change answer beside a source neither side could read (#784 review)."""
    remote = _remote(
        tmp_path,
        {".claude/settings.json": _BASE_SETTINGS, ".cursor/mcp.json": '{"mcpServers": {"docs": ',
         "README.md": "# demo\n"},
        {"docs-only": {"README.md": "# demo\nmore\n"}},
    )
    clone = _clone(remote, "docs-only")
    code, output = _diff(clone)
    assert code == 0, output
    assert _quoted(QUICKSTART, "not_compared") == [_normalize(output)]
    code, payload = _diff(clone, "--json")
    assert '"comparison_status": "comparable"' in payload
    assert '"limit": "parse_failed"' in payload


def test_the_quoted_incomparable_answer_is_what_diff_prints(documented_remote: Path) -> None:
    clone = _clone(documented_remote, "broken")
    code, output = _diff(clone)
    assert code == 0, output
    assert _blocks(QUICKSTART, "Cannot compare against") == [_normalize(output)]
    code, payload = _diff(clone, "--json")
    assert '"comparison_status": "incomparable"' in payload
    assert "head_inventory_incomplete" in payload


#: The README sentence that claims `diff`'s text and its `--json` describe one
#: run alike. Held to the one answer where they do not (review cycle 6).
_PARITY_CLAIM = "`--json` publishes the same entries, counters, question and command"


def test_the_readme_parity_sentence_carves_out_the_refusal(documented_remote: Path) -> None:
    """A refusal prints the reference lines and publishes no `review`.

    So the question, the counters and the command have no JSON counterpart
    there, and the sentence claiming a script and a reader describe one run the
    same way has to say so in its own breath — otherwise the page promises a
    key a script will not find on the answer hardest to check. Cycles 4 and 5
    found the same shape in the migration note and the Action's page; this
    holds the claim to the behaviour rather than to another copy of the prose.
    """

    clone = _clone(documented_remote, "broken")
    code, output = _diff(clone)
    assert code == 0, output
    printed = _normalize(output)
    assert printed[0].startswith("Cannot compare against ")
    # The text names its own provenance on a refusal (#812 follow-up) ...
    assert printed[-2].startswith("Inputs: base <sha>")
    assert printed[-1].startswith("Reproduce")
    # ... while the JSON publishes no block those two lines could come from.
    code, payload = _diff(clone, "--json")
    published = json.loads(payload)
    assert published["review"] is None
    assert published["base_commit"], "the command the text prints is built from this"

    collapsed = " ".join(README.read_text(encoding="utf-8").split())
    assert _PARITY_CLAIM in collapsed, (
        "README.md no longer makes the text/JSON parity claim this guard "
        "carves out; re-point it at the sentence that replaced it."
    )
    sentence = collapsed[collapsed.index(_PARITY_CLAIM) :].split(". ", 1)[0]
    assert "publishes no `review`" in sentence, (
        "README.md claims `--json` publishes the question and command beside "
        "the rows without naming the refusal, which publishes `review: null` "
        "while its text still prints both reference lines. Give that sentence "
        "the carve-out STABILITY.md already carries."
    )


def _with_base_url(url: str) -> str:
    return _BASE_SETTINGS.replace(
        '  "permissions"', f'  "env": {{"ANTHROPIC_BASE_URL": "{url}"}},\n  "permissions"'
    )


def test_the_quoted_no_compared_grant_block_is_what_diff_prints(tmp_path: Path) -> None:
    """The zero-row `env` value edit the quickstart says is not reported as no change (#812).

    A value-only edit: the inventory redacts the value, so only the byte
    identity proof shows the file changed (review cycle 2).
    """
    remote = _remote(
        tmp_path,
        {".claude/settings.json": _with_base_url("https://api.anthropic.com"), "README.md": "# demo\n"},
        {"env-only": {".claude/settings.json": _with_base_url("https://proxy.example")}},
    )
    code, output = _diff(_clone(remote, "env-only"))
    assert code == 0, output
    printed = _normalize(output)
    assert _NO_CHANGE in printed
    quoted = _blocks(QUICKSTART, "What this run established:")
    assert len(quoted) == 1
    # The page quotes the block alone; the answer now also ends with the
    # compared commits and the reproduction, on every result (#812 follow-up),
    # so the quote is a run of the printed lines rather than its last ones.
    start = printed.index(quoted[0][0])
    assert printed[start : start + len(quoted[0])] == quoted[0]


def test_the_documented_missing_base_recovery_holds(documented_remote: Path) -> None:
    """The quickstart's recovery, in the single-branch clone it describes."""
    clone = _clone(documented_remote, "change", "--single-branch", "--branch", "change")
    quickstart = " ".join(QUICKSTART.read_text(encoding="utf-8").split())

    code, output = _diff(clone)
    assert code == 2
    documented = " ".join(
        "Invalid value for --base: No base ref could be detected. Pass --base <ref> "
        "explicitly (use --base HEAD for uncommitted changes only).".split()
    )
    assert documented in _flat(output) and documented in quickstart

    # The trap the page warns about: a plain fetch updates only FETCH_HEAD.
    _git(clone, "fetch", "-q", "origin", "main")
    code, output = _diff(clone, "--base", "origin/main")
    assert code == 2 and "not available locally" in _flat(output)

    recovery = "git fetch origin main:refs/remotes/origin/main"
    assert recovery in quickstart
    _git(clone, *recovery.split()[1:])
    code, output = _diff(clone, "--base", "origin/main")
    assert code == 0, output
    assert _normalize(output) == _quoted(QUICKSTART, "change")[0]


def test_the_documented_partial_clone_recovery_holds(documented_remote: Path) -> None:
    """The quickstart's partial-clone sentence, in the blobless clone it describes (#817)."""
    _git(documented_remote, "config", "uploadpack.allowFilter", "true")
    clone = documented_remote.parent / "clone-blobless"
    _git(documented_remote.parent, "clone", "-q", "--filter=blob:none", "--no-checkout",
         documented_remote.as_uri(), str(clone))
    _git(clone, "checkout", "-q", "change")
    quickstart = " ".join(QUICKSTART.read_text(encoding="utf-8").split())
    assert "A partial clone (`git clone --filter=blob:none`)" in quickstart

    recovery = "git fetch --refetch --no-filter origin"
    code, output = _diff(clone, "--base", "origin/main")
    assert code == 2 and "Traceback" not in output
    assert f"`{recovery}`" in quickstart
    assert recovery.replace("git ", f"git -C {clone} ", 1) in _flat(output)

    # The trap the page warns about: `--refetch` alone keeps the filter.
    trap = "git fetch --refetch origin"
    assert f"`{trap}` alone keeps the clone's filter" in quickstart
    _git(clone, *trap.split()[1:])
    code, output = _diff(clone, "--base", "origin/main")
    assert code == 2 and "objects_missing" in output

    _git(clone, *recovery.split()[1:])
    code, output = _diff(clone, "--base", "origin/main")
    assert code == 0, output
    assert _normalize(output) == _quoted(QUICKSTART, "change")[0]


#: The label an entry page puts on quotes it took from a published build.
_PUBLISHED_LABEL = re.compile(r"from the published `(\d+\.\d+\.\d+[^`]*)`", re.I)
#: The published build a not-yet-released label compares its quotes with.
_COMPARED_RELEASE = re.compile(r"\bthe published `(\d+\.\d+\.\d+[^`]*)`", re.I)
#: The version a quoted `Compared:` / `Inputs:` line records.
_REFERENCE_VERSION = re.compile(r"^(?:Compared|Inputs): .*, agents-shipgate (\S+?)\.$", re.M)
#: The first line of each `diff` answer the entry pages quote, whole or in part.
_ANSWER_PREFIXES = ("Agent capability diff", "Cannot compare against", "What this run established:")

#: The release `_PUBLISHED_ANSWERS` was recorded from.
_PUBLISHED_ANSWERS_VERSION = "1.1.0"
#: What that release prints for each documented answer, as the sha256 of the
#: answer after `_normalize` (`_answer_digest`). `no_compared_grant` is the
#: `What this run established` block of the `env`-only edit, the part the
#: quickstart quotes. Recorded at docs/release-runbook.md § Cutting the release,
#: step 8, by running the published build, installed from PyPI into a clean
#: virtualenv outside any checkout, on the fixtures this module builds. Never
#: recorded from this tree: until its next version bump it prints the published
#: version on its reference lines, so a record taken from it would let a
#: source-tree capture pass as published output.
_PUBLISHED_ANSWERS = {
    "change": "536d404fdbd22382afd84f6b6ef6f214b6020499132d4b672acb4467531e2411",
    "no_change": "4735bd240c7bdfbb46655f144d7fa0905d621de053564ab3d609972994df6d74",
    "not_compared": "73d814a7a17a6447006e7670f9a8030cfcd80ba599b0dbd3fc106367237337c1",
    "incomparable": "6be1e130dae73c6c8d51ec9d27623a98093d83270545929692c227b18c897883",
    "no_compared_grant": "88531dd066be3ba7c91226acee3286c4a155b29e50a35e598063cae4efae272b",
}


def _answer_digest(block: list[str]) -> str:
    """The sha256 `_PUBLISHED_ANSWERS` records for one normalized answer."""
    return hashlib.sha256("\n".join(block).encode("utf-8")).hexdigest()


def _answers(text: str) -> list[str]:
    """The `diff` answers a page quotes, as written."""
    blocks = re.findall(r"```text\n(.*?)\n```", text, re.S)
    return [block for block in blocks if block.startswith(_ANSWER_PREFIXES)]


def _published_quote_problems(
    text: str, published: frozenset[str] = frozenset(_PUBLISHED_ANSWERS.values())
) -> list[str]:
    """What is wrong with a page's claim about which build its quotes came from.

    Two phases, and a page is in exactly one. Between an output change and the
    release that ships it, quotes come from the source tree: the page says they
    are not yet released and names the published build it compares them with.
    After publication they are re-captured from the published build
    (docs/release-runbook.md § Cutting the release, step 8), and the page names
    that build. This reads:

    - the versions. A published label, and every `Compared:` / `Inputs:` line
      under it, must name the newest published release. A not-yet-released
      label must name that release as the one it compares its quotes with;
    - each normalized answer, held to ``published``, the answers recorded from
      that release. Only this tells a source-tree capture from the published
      output: normalizing the version, as the byte comparisons above must,
      hides it, and until its next version bump the tree prints the published
      version anyway. A page that calls its quotes published output may quote no
      answer outside the record. A page that calls them not yet released must
      quote at least one.

    It cannot read how the record was taken. That is step 8's capture from the
    published build, reviewed in the change that re-pins `_PUBLISHED_ANSWERS`.
    """

    flat = " ".join(text.split())
    answers = _answers(text)
    quoted = {match.group(1) for block in answers for match in _REFERENCE_VERSION.finditer(block)}
    unpublished = [
        block.splitlines()[0]
        for block in answers
        if _answer_digest(_normalize(block)) not in published
    ]
    labels = set(_PUBLISHED_LABEL.findall(flat))
    newest = LATEST_PUBLISHED_VERSION
    problems: list[str] = []
    if not quoted:
        problems.append("quotes no `Compared:` or `Inputs:` line")
    if not labels:
        unreleased = [
            " ".join(paragraph.split())
            for paragraph in re.split(r"\n[ \t]*\n", text)
            if "not yet released" in paragraph.lower()
        ]
        if not unreleased:
            problems.append(
                "neither names the published build its quotes came from nor "
                "says they are not yet released"
            )
            return problems
        compared = {
            version for paragraph in unreleased for version in _COMPARED_RELEASE.findall(paragraph)
        }
        if compared != {newest}:
            problems.append(
                f"says its quotes are not yet released and compares them with the "
                f"published {sorted(compared) or 'build it never names'}; the newest "
                f"published release is {newest}. Name it, and say what it prints instead"
            )
        if answers and not unpublished:
            problems.append(
                f"says its quotes are not yet released, but each is an answer the "
                f"published {newest} prints (`_PUBLISHED_ANSWERS`). Label them as from "
                f"the published `{newest}`"
            )
        return problems
    if labels != {newest}:
        problems.append(
            f"labels its quotes as from the published {sorted(labels)}; the newest "
            f"published release is {newest}. Re-capture them from it"
        )
    if quoted != {newest}:
        problems.append(
            f"calls its quotes published output, but their reference lines record "
            f"{sorted(quoted)}, not {newest}"
        )
    if unpublished:
        problems.append(
            f"calls its quotes published output, but {len(unpublished)} of them are not "
            f"an answer the published {newest} prints (`_PUBLISHED_ANSWERS`): "
            f"{unpublished}. Label a re-capture from the source tree as not yet "
            f"released; only step 8 re-captures from a published build and re-pins"
        )
    if "not yet released" in flat.lower():
        problems.append("says its quotes are both published and not yet released")
    return problems


def test_quotes_labelled_published_come_from_the_newest_release() -> None:
    """#778: the entry pages quote the published build, and say which one."""

    for page in (README, QUICKSTART):
        problems = _published_quote_problems(page.read_text(encoding="utf-8"))
        assert not problems, f"{page.relative_to(REPO_ROOT)}: " + "; ".join(problems)


def test_the_published_answers_record_the_newest_release() -> None:
    """Step 8 re-takes the record each time a release is published."""

    assert _PUBLISHED_ANSWERS_VERSION == LATEST_PUBLISHED_VERSION, (
        f"_PUBLISHED_ANSWERS records {_PUBLISHED_ANSWERS_VERSION}, but the newest "
        f"published release is {LATEST_PUBLISHED_VERSION}. Install it from PyPI into "
        "a clean virtualenv outside any checkout, run its `diff` on the fixtures "
        "this module builds, and record `_answer_digest` of each normalized answer"
    )
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in _PUBLISHED_ANSWERS.values())


#: The labels the pages carried when `v1.1.0` was tagged: source-tree quotes,
#: compared with the `1.0.0` that was then the newest release. Once `1.1.0`
#: shipped and printed those same answers, both labels were stale (#853 review).
_README_LABEL_AS_TAGGED = """\
**Not yet released:** the output above is from this repository's source tree,
which still reports version `1.1.0`, run in a clone. The published `1.0.0` from
PyPI names the same changes as four rows, without the dispositions, the joined
replacement, the MCP launch details, the `What this run established` block, the
review question and the reference lines."""
_QUICKSTART_LABEL_AS_TAGGED = """\
**The answers below are not yet released:** they are from this repository's
source tree, which still reports version `1.1.0`, run in a clone outside any
source checkout of this project. The published `1.0.0` names the same changes
as four rows, without the dispositions, the joined replacement, the MCP launch
details, the review question and the reference lines, and none of its answers
has the `What this run established` block."""


def test_the_published_quote_guard_catches_a_stale_capture() -> None:
    """Negative controls: each stale state this guard exists for is reported.

    Built from the pages' own quotes, and judged against a record of exactly
    the quotes each control treats as the published build's output, so the
    controls hold in either phase the pages are in.
    """

    newest = LATEST_PUBLISHED_VERSION
    readme = _answers(README.read_text(encoding="utf-8"))
    quickstart = _answers(QUICKSTART.read_text(encoding="utf-8"))
    assert len(readme) == 1 and len(quickstart) == 5
    published = frozenset(_answer_digest(_normalize(block)) for block in readme + quickstart)
    # A source-tree capture after an output change: the same reference lines,
    # and one printed line the published build does not print.
    tree = [readme[0].replace("\n", "\nA line only the source tree prints.\n", 1)]

    def page(label: str, blocks: list[str], version: str = newest) -> list[str]:
        restamped = [_VERSION.sub(f"agents-shipgate {version}", block) for block in blocks]
        body = "\n\n".join(f"```text\n{block}\n```" for block in restamped)
        return _published_quote_problems(f"{label}\n\n{body}\n", published)

    def reports(problems: list[str], fragment: str) -> bool:
        return any(fragment in problem for problem in problems)

    released = f"**Released in `{newest}`:** from the published `{newest}`, installed from PyPI."
    unreleased = (
        f"**Not yet released:** from this repository's source tree. The published "
        f"`{newest}` names the same changes as four rows."
    )
    # Each phase, labelled as it is.
    assert not page(released, readme), page(released, readme)
    assert not page(released, quickstart), page(released, quickstart)
    assert not page(unreleased, tree), page(unreleased, tree)

    # A source-tree capture under the published label: its version strings are
    # the published build's, its answer is not (#853 review, case b).
    assert reports(page(released, tree), "not an answer the published")
    assert reports(page(released, quickstart + tree), "1 of them are not an answer")

    # The labels `v1.1.0` was tagged with, over answers `1.1.0` prints (#853
    # review, case a): they compare with a release that is no longer the
    # newest, and they call published output not yet released.
    for label, blocks in ((_README_LABEL_AS_TAGGED, readme), (_QUICKSTART_LABEL_AS_TAGGED, quickstart)):
        problems = page(label, blocks)
        assert reports(problems, "compares them with the published ['1.0.0']"), problems
        assert reports(problems, "each is an answer the published"), problems
    # Naming the newest release does not rescue a not-yet-released label over
    # answers that release prints, and naming none is wrong over answers it
    # does not.
    assert reports(page(unreleased, readme), "each is an answer the published")
    assert reports(page("**Not yet released:** from the source tree.", tree), "build it never names")

    # A label left naming an older release after a newer one shipped.
    assert reports(page("From the published `0.0.1`.", readme, "0.0.1"), "Re-capture them from it")
    # The right label over reference lines another build recorded.
    assert reports(page(released, readme, "0.0.1"), "reference lines record ['0.0.1']")
    # Neither label, and both at once.
    assert reports(page("From the source tree.", tree), "nor says they are not yet released")
    assert reports(
        page(f"Not yet released, from the published `{newest}`.", readme),
        "both published and not yet released",
    )


def _table_row(text: str, first_cell: str) -> str:
    rows = [line for line in text.splitlines() if line.startswith(f"| {first_cell} |")]
    assert len(rows) == 1, f"expected one table row starting {first_cell!r}, found {len(rows)}"
    return rows[0]


def test_entry_pages_state_the_declared_channel_of_the_published_release() -> None:
    """A `v*` tag's shape no longer decides its line (Amendment 5).

    Before #779 the README's channel table installed the advisory line only
    from a preview pre-release and gave the qualified line every `v*` tag,
    while `v1.0.0` — a `v*` tag on PyPI — was declared advisory. The rows are
    read, not phrases, so rewording a cell cannot slip past the check.
    """
    channel = load_declaration(REPO_ROOT / ".github/release-channels.json")[LATEST_PUBLISHED_VERSION]
    assert channel in {"advisory", "qualified"}
    published = f"`v{LATEST_PUBLISHED_VERSION}`"
    readme = README.read_text(encoding="utf-8")
    distribution = (REPO_ROOT / "docs/distribution.md").read_text(encoding="utf-8")
    for name, text in (("README.md", readme), ("docs/distribution.md", distribution)):
        advisory = _table_row(text, "**Advisory**")
        qualified = _table_row(text, "**Qualified gate**")
        install = advisory.split("|")[3]
        gate_install = " ".join(qualified.split("|")[3].split())
        # The exact pre-#779 cell: every `v*` tag on the qualified line.
        assert gate_install != "a `v*` release tag", f"{name}: the qualified row claims every `v*` tag"
        assert "qualified line" in gate_install, f"{name}: the qualified row does not name its line"
        if channel == "advisory":
            assert published in install, f"{name}: the advisory install cell omits {published}"
            assert "preview" in install, f"{name}: the preview channel left the advisory row"
            if published in gate_install:
                assert f"{published} is not one" in gate_install, (
                    f"{name}: the qualified row claims {published}"
                )
        else:
            assert published not in install, f"{name}: the advisory row claims a qualified {published}"
    cadence = " ".join(distribution.split())
    assert "reads each `v*` tag's declared channel" in cadence, (
        "docs/distribution.md no longer says the cadence splits `v*` tags by declared channel"
    )
    flat = " ".join(readme.split())
    article = "an" if channel[0] in "aeiou" else "a"
    assert f"{published} is {article} {channel} release" in flat
    assert "Status: pre-1.0" not in flat
    assert "tells you whether it can merge" not in flat
