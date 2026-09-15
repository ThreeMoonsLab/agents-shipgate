"""#779: the first task the entry pages teach is the output `diff` prints.

The README and the quickstart open on `agents-shipgate diff` against a
permission/MCP change and quote each of its answers. Those quotes were taken
from the published `1.0.0`, installed outside a checkout and run in a clone of
a repository with a remote. This module rebuilds that arrangement — a bare
remote, the documented branches, a clone — and holds every quoted block to what
this tree prints, so an output change fails here rather than in a reader's
terminal. The base commit abbreviation in the header is the only normalized
field.

It also holds the channel statements the entry pages make to the declaration
that decides them, `.github/release-channels.json`, rather than to a second
copy of the answer.
"""

from __future__ import annotations

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
_SHA = re.compile(r"\(([0-9a-f]{8})\)")
_NO_CHANGE = "No static host-grant changes detected. No verdict is implied."


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=Docs", "-c", "user.email=docs@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
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
    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), *args])
    return result.exit_code, result.output


def _normalize(text: str) -> list[str]:
    return [_SHA.sub("(<sha>)", line.rstrip()) for line in text.strip().splitlines()]


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


def test_the_documented_missing_base_recovery_holds(documented_remote: Path) -> None:
    """The quickstart's recovery, in the single-branch clone it describes."""
    clone = _clone(documented_remote, "change", "--single-branch", "--branch", "change")
    quickstart = " ".join(QUICKSTART.read_text(encoding="utf-8").split())

    code, output = _diff(clone)
    assert code == 2
    flat = " ".join(re.sub(r"[│╭╮╰╯─]", " ", output).split())
    documented = " ".join(
        "Invalid value for --base: No base ref could be detected. Pass --base <ref> "
        "explicitly (use --base HEAD for uncommitted changes only).".split()
    )
    assert documented in flat and documented in quickstart

    # The trap the page warns about: a plain fetch updates only FETCH_HEAD.
    _git(clone, "fetch", "-q", "origin", "main")
    code, output = _diff(clone, "--base", "origin/main")
    assert code == 2 and "not available locally" in output

    recovery = "git fetch origin main:refs/remotes/origin/main"
    assert recovery in quickstart
    _git(clone, *recovery.split()[1:])
    code, output = _diff(clone, "--base", "origin/main")
    assert code == 0, output
    assert _normalize(output) == _quoted(QUICKSTART, "change")[0]


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
    channel = load_declaration()[LATEST_PUBLISHED_VERSION]
    assert channel in {"advisory", "qualified"}
    published = f"`v{LATEST_PUBLISHED_VERSION}`"
    readme = README.read_text(encoding="utf-8")
    distribution = (REPO_ROOT / "docs/distribution.md").read_text(encoding="utf-8")
    for name, text in (("README.md", readme), ("docs/distribution.md", distribution)):
        advisory = _table_row(text, "**Advisory**")
        qualified = _table_row(text, "**Qualified gate**")
        install = advisory.split("|")[3]
        if channel == "advisory":
            assert published in install, f"{name}: the advisory install cell omits {published}"
            assert "preview" in install, f"{name}: the preview channel left the advisory row"
            gate_install = qualified.split("|")[3]
            assert published not in gate_install or "not" in gate_install, (
                f"{name}: the qualified row claims {published}"
            )
    flat = " ".join(readme.split())
    article = "an" if channel[0] in "aeiou" else "a"
    assert f"{published} is {article} {channel} release" in flat
    assert "Status: pre-1.0" not in flat
    assert "tells you whether it can merge" not in flat
