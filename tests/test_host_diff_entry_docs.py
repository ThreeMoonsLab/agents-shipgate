"""#779: the first task the entry pages teach is the output `diff` prints.

The README and the quickstart open on `agents-shipgate diff` against a
permission/MCP change and quote each of its answers. The no-change, not-compared
and cannot-compare quotes were first taken from the published `1.0.0`,
installed outside a checkout and run in a clone of a repository with a remote;
the change quote is this tree's output since #795, every quote since #812 adds
the `What this run established` block, and the pages label them as not yet
released. This module rebuilds that arrangement — a bare remote, the documented
branches, a clone — and holds every quoted block to what this tree prints, so an
output change fails here rather than in a reader's terminal. Commit ids and the
version on the reference lines are the only normalized fields.

It also holds the channel statements the entry pages make to the declaration
that decides them, `.github/release-channels.json`, rather than to a second
copy of the answer.
"""

from __future__ import annotations

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


def test_the_quoted_unread_field_block_is_what_diff_prints(tmp_path: Path) -> None:
    """The zero-row `env` edit the quickstart says is not reported as no change (#812)."""
    remote = _remote(
        tmp_path,
        {".claude/settings.json": _BASE_SETTINGS, "README.md": "# demo\n"},
        {"env-only": {".claude/settings.json": _BASE_SETTINGS.replace(
            '  "permissions"', '  "env": {"ANTHROPIC_BASE_URL": "https://proxy.example"},\n  "permissions"'
        )}},
    )
    code, output = _diff(_clone(remote, "env-only"))
    assert code == 0, output
    printed = _normalize(output)
    assert _NO_CHANGE in printed
    quoted = _blocks(QUICKSTART, "What this run established:")
    assert len(quoted) == 1
    assert printed[-len(quoted[0]):] == quoted[0]


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
