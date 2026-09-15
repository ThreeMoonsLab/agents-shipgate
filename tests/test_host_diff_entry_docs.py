"""#779: the first task the entry pages teach is the output `diff` prints.

The README and the quickstart open on `agents-shipgate diff` against a
permission/MCP change, and quote its three answers. Those quotes were taken
from the published `1.0.0` installed outside a checkout; this module rebuilds
the documented fixture and holds every quoted block to what this tree prints,
so an output change fails here rather than in a reader's terminal. The commit
abbreviation in the header is the only normalized field.

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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Docs", "-c", "user.email=docs@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def documented_repo(tmp_path: Path) -> Path:
    """The two-commit repository the quickstart describes, on its `main`."""
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".claude/settings.json").write_text(_BASE_SETTINGS)
    (repo / ".mcp.json").write_text(_BASE_MCP)
    (repo / "README.md").write_text("# demo\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _branch(repo: Path, name: str, files: dict[str, str]) -> None:
    _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", name)
    for relative, text in files.items():
        (repo / relative).write_text(text)
    _git(repo, "commit", "-qam", name)


def _diff(repo: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), *args])
    return result.exit_code, result.output


def _normalize(text: str) -> list[str]:
    return [_SHA.sub("(<sha>)", line.rstrip()) for line in text.strip().splitlines()]


def _text_blocks(path: Path, first_line_prefix: str) -> list[list[str]]:
    blocks = re.findall(r"```text\n(.*?)\n```", path.read_text(encoding="utf-8"), re.S)
    return [_normalize(block) for block in blocks if block.startswith(first_line_prefix)]


def test_the_quoted_change_rows_are_what_diff_prints(documented_repo: Path) -> None:
    _branch(documented_repo, "change", {".claude/settings.json": _HEAD_SETTINGS, ".mcp.json": _HEAD_MCP})
    code, output = _diff(documented_repo)
    assert code == 0, output
    printed = _normalize(output)
    changed = [
        block
        for path in (README, QUICKSTART)
        for block in _text_blocks(path, "Agent capability diff")
        if "No static host-grant changes detected. No verdict is implied." not in block
    ]
    assert len(changed) == 2, "README.md and docs/quickstart.md each quote the change rows once"
    for block in changed:
        assert block == printed


def test_the_quoted_no_change_answer_is_what_diff_prints(documented_repo: Path) -> None:
    _branch(documented_repo, "docs-only", {"README.md": "# demo\nmore\n"})
    code, output = _diff(documented_repo)
    assert code == 0, output
    quiet = [
        block for block in _text_blocks(QUICKSTART, "Agent capability diff")
        if "No static host-grant changes detected. No verdict is implied." in block
    ]
    assert quiet == [_normalize(output)]


def test_the_quoted_incomparable_answer_is_what_diff_prints(documented_repo: Path) -> None:
    _branch(documented_repo, "broken", {".mcp.json": '{"mcpServers": {"docs": '})
    code, output = _diff(documented_repo)
    assert code == 0, output
    assert _text_blocks(QUICKSTART, "Cannot compare against") == [_normalize(output)]
    code, payload = _diff(documented_repo, "--json")
    assert '"comparison_status": "incomparable"' in payload
    assert "head_inventory_incomplete" in payload


def test_the_documented_missing_base_recovery_holds(tmp_path: Path) -> None:
    repo = tmp_path / "single-branch"
    (repo / ".claude").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "feature")
    (repo / ".claude/settings.json").write_text("{}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    code, output = _diff(repo)
    assert code == 2
    flat = " ".join(re.sub(r"[│╭╮╰╯─]", " ", output).split())
    documented = " ".join(
        "Invalid value for --base: No base ref could be detected. Pass --base <ref> "
        "explicitly (use --base HEAD for uncommitted changes only).".split()
    )
    assert documented in flat
    assert documented in " ".join(QUICKSTART.read_text(encoding="utf-8").split())
    code, output = _diff(repo, "--base", "HEAD")
    assert code == 0, output


def test_entry_pages_state_the_declared_channel_of_the_published_release() -> None:
    """A `v*` tag's shape no longer decides its line (Amendment 5).

    Before #779 the README's channel table installed the advisory line only
    from a preview pre-release and gave the qualified line every `v*` tag,
    while `v1.0.0` — a `v*` tag on PyPI — was declared advisory.
    """
    channel = load_declaration()[LATEST_PUBLISHED_VERSION]
    readme = " ".join(README.read_text(encoding="utf-8").split())
    distribution = " ".join((REPO_ROOT / "docs/distribution.md").read_text(encoding="utf-8").split())
    article = "an" if channel[0] in "aeiou" else "a"
    assert f"`v{LATEST_PUBLISHED_VERSION}` is {article} {channel} release" in readme
    for name, text in (("README.md", readme), ("docs/distribution.md", distribution)):
        assert "| the unqualified preview pre-release |" not in text, (
            f"{name} names the preview as the only advisory install"
        )
        assert "| a `v*` release tag |" not in text, (
            f"{name} puts every `v*` tag on the qualified line"
        )
    assert "Status: pre-1.0" not in readme
