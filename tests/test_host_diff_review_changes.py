"""#795 slice 1: the text projections name the concrete change and a review question.

An unfamiliar reviewer reading `diff`, `verify`'s text or the PR comment must be
able to name the rule and its disposition, the before and after of a replacement
the engine established, an MCP server's published launch difference, the
compared commits, and one question to answer, without a maintainer translating.

Each case is a real two-commit repository driven through the documented
commands. What is pinned is what the reader is shown. The JSON rows every route
publishes are held unchanged beside it: this slice adds no row, field or value.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate import __version__
from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import review_changes
from agents_shipgate.report.host_comparison import (
    comparison_reference_lines,
    host_comparison_lines,
)
from agents_shipgate.schemas.host_comparison import HostComparison

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
SETTINGS = ".claude/settings.json"
SUBJECT = "claude-code .claude/settings.json"
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _repository(tmp_path: Path, base: dict[str, object], head: dict[str, object]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, value in base.items():
        _write(repo, name, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "change")
    for name, value in head.items():
        _write(repo, name, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    return repo


def _write(repo: Path, name: str, value: object) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _invoke(args: list[str]) -> str:
    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1", "COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return result.output


def _diff(repo: Path) -> tuple[str, dict]:
    workspace = ["diff", "--workspace", str(repo), "--base", "main"]
    return _invoke(workspace), json.loads(_invoke([*workspace, "--json"]))


def _verify(repo: Path, out: Path, *, head: bool = True) -> tuple[list[str], list[str], dict]:
    """Verify's text block, the PR comment's summary block, and verifier.json."""

    args = [
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--pr-comment-style", "capability-review", "--format", "text",
        "--base", "main",
    ]
    if head:
        args += ["--head", _git(repo, "rev-parse", "HEAD")]
    text = _invoke(args).splitlines()
    block = text[: text.index("Agents Shipgate verify: advisory: no application release policy configured")]
    comment = (out / "pr-comment.md").read_text(encoding="utf-8").splitlines()
    summary = comment[comment.index("### Human summary") + 1 : comment.index(
        "Advisory: no application release policy configured. This comparison grants no merge authority."
    )]
    return block, summary, json.loads((out / "verifier.json").read_text(encoding="utf-8"))


def _check(repo: Path, *selection: str) -> list[str]:
    """`check`'s host comparison text, before its control headline."""

    if not selection:
        selection = ("--base", "main", "--head", _git(repo, "rev-parse", "HEAD"))
    lines = _invoke(
        ["check", "--workspace", str(repo), *selection, "--format", "text"]
    ).splitlines()
    return lines[: next(i for i, line in enumerate(lines) if line.startswith("Control: "))]


def _plain(markdown: list[str]) -> list[str]:
    """PR comment lines as the verify text prints them: inline code unwrapped, no blank lines."""

    return [
        re.sub(r"(`+) (.*?) \1", r"\2", line).replace("`", "")
        for line in markdown
        if line.strip()
    ]


def _table_entry(text: str, header: str) -> list[str]:
    """One `diff` entry: its header line and the two lines under it, unpadded."""

    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if " ".join(line.split()) == header)
    return [" ".join(line.split()) for line in lines[index : index + 3]]


# --- permission rules: disposition, replacement and move --------------------

#: (base allow/deny, head allow/deny, ⚠, severity, direction, before → after, why,
#:  JSON rows as (direction, before, after, expands))
JOINED = {
    "widened": (
        {"permissions": {"allow": ["Bash(npm test *)"]}},
        {"permissions": {"allow": ["Bash(npm *)"]}},
        True, "medium", "widened",
        "allow: Bash(npm test *) → allow: Bash(npm *)",
        "the new rule matches everything the old rule matched; runs without a prompt",
        [("added", "—", "Bash(npm *)", True), ("removed", "Bash(npm test *)", "—", False)],
    ),
    "narrowed": (
        {"permissions": {"allow": ["Bash(npm *)"]}},
        {"permissions": {"allow": ["Bash(npm test *)"]}},
        False, "medium", "narrowed",
        "allow: Bash(npm *) → allow: Bash(npm test *)",
        "the new rule matches only what the old rule matched; runs without a prompt",
        [("added", "—", "Bash(npm test *)", False), ("removed", "Bash(npm *)", "—", False)],
    ),
    # The legacy `:*` spelling reads as the documented trailing ` *` (#816).
    "narrowed_colon": (
        {"permissions": {"allow": ["Bash(npm:*)"]}},
        {"permissions": {"allow": ["Bash(npm test:*)"]}},
        False, "medium", "narrowed",
        "allow: Bash(npm:*) → allow: Bash(npm test:*)",
        "the new rule matches only what the old rule matched; runs without a prompt",
        [("added", "—", "Bash(npm test:*)", False), ("removed", "Bash(npm:*)", "—", False)],
    ),
    "moved_to_allow": (
        {"permissions": {"deny": ["Bash(git push *)"]}},
        {"permissions": {"allow": ["Bash(git push *)"]}},
        True, "medium", "moved",
        "deny: Bash(git push *) → allow: Bash(git push *)",
        "the same rule moved from deny to allow; runs without a prompt",
        [("added", "—", "Bash(git push *)", True), ("removed", "Bash(git push *)", "—", True)],
    ),
    "moved_to_deny": (
        {"permissions": {"allow": ["Bash(git push *)"]}},
        {"permissions": {"deny": ["Bash(git push *)"]}},
        False, "medium", "moved",
        "allow: Bash(git push *) → deny: Bash(git push *)",
        "the same rule moved from allow to deny; a denial the agent is subject to",
        [("added", "—", "Bash(git push *)", False), ("removed", "Bash(git push *)", "—", False)],
    ),
}


def _json_rows(rows: list[dict]) -> list[tuple[str, str, str, bool]]:
    return sorted((row["direction"], row["before"], row["after"], row["expands"]) for row in rows)


@pytest.mark.parametrize("name", list(JOINED))
def test_a_replacement_or_move_the_engine_established_reads_as_one_change(
    tmp_path: Path, name: str
) -> None:
    base, head, expands, severity, direction, transition, why, published = JOINED[name]
    repo = _repository(tmp_path, {SETTINGS: base}, {SETTINGS: head})
    marker = "⚠ " if expands else ""

    text, payload = _diff(repo)
    assert _table_entry(text, f"{marker}{severity} {direction} {SUBJECT}") == [
        f"{marker}{severity} {direction} {SUBJECT}", transition, why,
    ]
    assert "1 change(s) from 2 rows" in text
    # The published rows are the engine's two, with the values 1.0.0 published.
    assert _json_rows(payload["rows"]) == sorted(published)

    block, summary, verifier = _verify(repo, tmp_path / "out")
    entry = [f"- {marker}{severity} / {direction} — {SUBJECT}", f"  {transition}", f"  {why}"]
    assert block[1:4] == entry
    assert _plain(summary) == _plain(block)
    assert _json_rows(verifier["host_comparison"]["rows"]) == sorted(published)


def test_moved_and_replaced_rules_in_one_edit_join_separately(tmp_path: Path) -> None:
    """#816's `moved` case: a deny-to-allow move beside a narrowing."""

    repo = _repository(
        tmp_path,
        {SETTINGS: {"permissions": {"allow": ["Bash(git status *)"], "deny": ["Bash(git log *)"]}}},
        {SETTINGS: {"permissions": {"allow": ["Bash(git status --short *)", "Bash(git log *)"]}}},
    )

    text, payload = _diff(repo)
    assert _table_entry(text, f"⚠ medium moved {SUBJECT}")[1] == (
        "deny: Bash(git log *) → allow: Bash(git log *)"
    )
    assert _table_entry(text, f"medium narrowed {SUBJECT}")[1] == (
        "allow: Bash(git status *) → allow: Bash(git status --short *)"
    )
    assert "2 change(s) from 4 rows, 1 widening what the agent may do (⚠)." in text
    assert len(payload["rows"]) == 4


def test_a_rule_a_move_and_a_replacement_both_claim_joins_the_replacement(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {SETTINGS: {"permissions": {"allow": ["Bash(npm *)"]}}},
        {SETTINGS: {"permissions": {"allow": ["Bash(npm test *)"], "deny": ["Bash(npm *)"]}}},
    )

    text, _ = _diff(repo)
    assert _table_entry(text, f"medium narrowed {SUBJECT}")[1] == (
        "allow: Bash(npm *) → allow: Bash(npm test *)"
    )
    assert _table_entry(text, f"low added {SUBJECT}")[1] == "deny: Bash(npm *)"
    assert "2 change(s) from 3 rows." in text


LOCAL = ".claude/settings.local.json"


@pytest.mark.parametrize(
    ("base", "head", "entries"),
    [
        # The same text leaving one settings file and arriving in another is not
        # a move: a move stays within one host and source.
        pytest.param(
            {SETTINGS: {"permissions": {"deny": ["Bash(git push *)"]}}},
            {SETTINGS: {"permissions": {}}, LOCAL: {"permissions": {"allow": ["Bash(git push *)"]}}},
            [
                ("⚠ medium added claude-code .claude/settings.local.json", "allow: Bash(git push *)"),
                (f"⚠ low removed {SUBJECT}", "deny: Bash(git push *) → gone"),
            ],
            id="another_source",
        ),
        # Removed from two dispositions: neither is the one it moved from.
        pytest.param(
            {SETTINGS: {"permissions": {"deny": ["Bash(git push *)"], "ask": ["Bash(git push *)"]}}},
            {SETTINGS: {"permissions": {"allow": ["Bash(git push *)"]}}},
            [
                (f"⚠ medium added {SUBJECT}", "allow: Bash(git push *)"),
                (f"⚠ low removed {SUBJECT}", "ask: Bash(git push *) → gone"),
                (f"⚠ low removed {SUBJECT}", "deny: Bash(git push *) → gone"),
            ],
            id="removed_from_two",
        ),
        # Added to two dispositions: neither is the one it moved to.
        pytest.param(
            {SETTINGS: {"permissions": {"deny": ["Bash(git push *)"]}}},
            {SETTINGS: {"permissions": {"allow": ["Bash(git push *)"], "ask": ["Bash(git push *)"]}}},
            [
                (f"⚠ medium added {SUBJECT}", "allow: Bash(git push *)"),
                (f"low added {SUBJECT}", "ask: Bash(git push *)"),
                (f"⚠ low removed {SUBJECT}", "deny: Bash(git push *) → gone"),
            ],
            id="added_to_two",
        ),
    ],
)
def test_identical_rule_text_joins_as_a_move_only_once_in_one_source(
    tmp_path: Path,
    base: dict[str, object],
    head: dict[str, object],
    entries: list[tuple[str, str]],
) -> None:
    repo = _repository(tmp_path, base, head)

    text, payload = _diff(repo)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    printed = sorted(
        (lines[index], lines[index + 1])
        for index, line in enumerate(lines)
        if line.endswith((SUBJECT, "settings.local.json")) and index + 1 < len(lines)
    )
    assert printed == sorted(entries)
    assert " moved " not in text and "change(s) from" not in text
    assert len(payload["rows"]) == len(entries)
    block, summary, _ = _verify(repo, tmp_path / "out")
    assert not any(" / moved — " in line for line in block)
    assert _plain(summary) == _plain(block)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # The lattice decides neither direction: no pair is invented.
        ("Bash(npm test *)", "Bash(make build *)"),
        # Rules are case-sensitive, so a case edit is two distinct rules.
        ("Bash(make Test *)", "Bash(make test *)"),
    ],
)
def test_an_undecided_replacement_stays_two_rows_with_their_dispositions(
    tmp_path: Path, before: str, after: str
) -> None:
    repo = _repository(
        tmp_path,
        {SETTINGS: {"permissions": {"allow": [before]}}},
        {SETTINGS: {"permissions": {"allow": [after]}}},
    )

    text, _ = _diff(repo)
    assert _table_entry(text, f"⚠ medium added {SUBJECT}")[1] == f"allow: {after}"
    assert _table_entry(text, f"medium removed {SUBJECT}")[1] == f"allow: {before} → gone"
    assert "2 change(s), 1 widening what the agent may do (⚠)." in text


def test_routes_that_redact_rule_arguments_never_join_rules_that_read_alike(tmp_path: Path) -> None:
    """`check` and a provided diff redact arguments, so a joined pair would read `X → X`."""

    repo = _repository(
        tmp_path,
        {SETTINGS: {"permissions": {"allow": ["Bash(npm test *)"], "deny": ["Bash(git push *)"]}}},
        {SETTINGS: {"permissions": {"allow": ["Bash(npm *)", "Bash(git push *)"]}}},
    )
    patch = tmp_path / "change.diff"
    patch.write_text(_git(repo, "diff", "main", "HEAD") + "\n", encoding="utf-8")

    for lines in (_check(repo), _check(repo, "--diff", str(patch))):
        joined = "\n".join(lines)
        assert "— → allow: Bash(<redacted-arguments>)" in joined
        assert "allow: Bash(<redacted-arguments>) → —" in joined
        assert "deny: Bash(<redacted-arguments>) → —" in joined
        assert " / widened — " not in joined and " / moved — " not in joined
        assert "npm" not in joined and "git push" not in joined
        assert lines[-1] == "Review question: Does the team intend these 4 declared capability changes?"
        assert not any(line.startswith(("Compared:", "Reproduce")) for line in lines)


# --- MCP servers: the published launch facts --------------------------------


def test_an_mcp_launch_change_names_its_published_difference(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {"gh": {"command": "npx", "args": ["-y", "gh-mcp"]}}}},
        {".mcp.json": {"mcpServers": {"gh": {
            "command": "docker", "args": ["run", "gh"],
            "env": {"GH_HOST": "github.example", "GH_TOKEN": GITHUB_TOKEN},
        }}}},
    )
    change = "gh: command name npx → docker; env keys +GH_HOST +GH_TOKEN"

    text, payload = _diff(repo)
    assert _table_entry(text, "⚠ high widened claude-code .mcp.json")[1] == change
    assert [(row["before"], row["after"]) for row in payload["rows"]] == [("gh", "gh")]

    block, summary, _ = _verify(repo, tmp_path / "out")
    assert block[2] == f"  {change}"
    assert _plain(summary) == _plain(block)
    check = _check(repo)
    assert f"  {change}" in check
    for output in (text, "\n".join(block), "\n".join(summary), "\n".join(check)):
        assert GITHUB_TOKEN not in output and "github.example" not in output


def test_an_added_remote_mcp_server_shows_its_redacted_endpoint(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {}}},
        {".mcp.json": {"mcpServers": {"linear": {
            "type": "http",
            "url": "https://ops:hunter2@mcp.linear.app/sse?token=abc123",
            "headers": {"Authorization": "Bearer sk-live-0123456789abcdef"},
        }}}},
    )
    cell = "linear (url https://mcp.linear.app/<redacted-path>; header keys Authorization)"

    text, payload = _diff(repo)
    assert _table_entry(text, "⚠ high added claude-code .mcp.json")[1] == cell
    assert [row["after"] for row in payload["rows"]] == ["linear"]
    block, summary, _ = _verify(repo, tmp_path / "out")
    assert block[2] == f"  — → {cell}"
    for output in (text, "\n".join(block), "\n".join(summary), "\n".join(_check(repo))):
        for secret in ("hunter2", "ops:", "abc123", "sk-live", "/sse"):
            assert secret not in output


def test_an_mcp_change_outside_the_published_fields_says_it_is_not_shown(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {"docs": {"command": "npx", "args": ["@example/docs@1.2.3"]}}}},
        {".mcp.json": {"mcpServers": {"docs": {"command": "npx", "args": ["@example/docs@latest"]}}}},
    )

    text, _ = _diff(repo)
    assert _table_entry(text, "⚠ high widened claude-code .mcp.json")[1] == (
        "docs: no difference in the command name npx, env key names or header key names; "
        "the change is in a detail this output does not show, such as the command's path "
        "or arguments"
    )
    assert "docs → docs" not in text and "latest" not in text


def _unshown(name: str, command: str) -> str:
    return (
        f"{name}: no difference in the command name {command}, env key names or header key "
        "names; the change is in a detail this output does not show, such as the command's "
        "path or arguments"
    )


def test_a_command_whose_path_changes_but_name_does_not_is_never_called_unchanged(
    tmp_path: Path,
) -> None:
    """A command server publishes only its command's name, so the text says so.

    `npx` → `./npx` swaps a command on PATH for one in the repository with the
    same arguments. The text must not read as though the command were unchanged
    and the arguments had changed, and an added `./tools/npx` must not read as
    though it launched `npx`.
    """

    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {
            "gh": {"command": "npx", "args": ["-y", "gh-mcp"]},
            "node": {"command": "/usr/local/bin/node", "args": ["server.js"]},
        }}},
        {".mcp.json": {"mcpServers": {
            "gh": {"command": "./npx", "args": ["-y", "gh-mcp"]},
            "node": {"command": "./scripts/node", "args": ["server.js"]},
            "billing": {"command": "./tools/npx", "env": {"BILLING_TOKEN": "x"}},
        }}},
    )
    expected = [_unshown("gh", "npx"), _unshown("node", "node")]
    added = "billing (command name npx; env keys BILLING_TOKEN)"

    text, payload = _diff(repo)
    changed = [
        " ".join(line.split())
        for line in text.splitlines()
        if line.strip().startswith(("gh:", "node:"))
    ]
    assert changed == expected
    assert _table_entry(text, "⚠ high added claude-code .mcp.json")[1] == added
    assert sorted((row["direction"], row["after"]) for row in payload["rows"]) == [
        ("added", "billing"), ("widened", "gh"), ("widened", "node"),
    ]

    block, summary, _ = _verify(repo, tmp_path / "out")
    for line in [*expected, f"— → {added}"]:
        assert f"  {line}" in block
    assert _plain(summary) == _plain(block)
    for output in (text, "\n".join(block), "\n".join(summary), "\n".join(_check(repo))):
        assert "unchanged" not in output
        assert "(command npx" not in output and "command npx →" not in output


def test_an_mcp_url_change_outside_the_published_fields_names_what_was_compared(
    tmp_path: Path,
) -> None:
    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {"remote": {"url": "https://mcp.example.com/v1?read_only=true"}}}},
        {".mcp.json": {"mcpServers": {"remote": {"url": "https://mcp.example.com/v1?read_only=false"}}}},
    )

    text, _ = _diff(repo)
    assert _table_entry(text, "⚠ high widened claude-code .mcp.json")[1] == (
        "remote: no difference in the url https://mcp.example.com/<redacted-path>, env key "
        "names or header key names; the change is in a detail this output does not show, "
        "such as the URL's query or another setting"
    )
    assert "read_only" not in text and "/v1" not in text


#: URL shapes the engine's URL sanitizer returns as written: Claude Code's
#: `${VAR}` expansion, a URL without a scheme, and a scheme it does not sanitize.
#: Each path is secret-shaped, as a webhook path can be (#723).
UNSANITIZED_URLS = {
    "hooks": "${SLACK_MCP_BASE}/hooks/T0SECRETSEGMENT/B0SECRET/SECRETVALUE",
    "bare": "mcp.example.com/hooks/T0SECRETPATHVALUE123/xyz",
    "custom": "custom://svcuser:hunter2@mcp.example.com/internal/SECRETPATH",
}
URL_SECRETS = (
    "SLACK_MCP_BASE", "T0SECRET", "B0SECRET", "SECRETVALUE", "SECRETPATH", "/hooks/", "/xyz",
    "/internal/", "svcuser", "hunter2", "custom://",
)


def _projections(repo: Path, out: Path) -> tuple[str, list[str], list[str], list[str]]:
    """`diff`, `verify`'s text block, the PR comment's summary and `check`, for one repository."""

    text, _ = _diff(repo)
    block, summary, _ = _verify(repo, out)
    assert _plain(summary) == _plain(block)
    return text, block, summary, _check(repo)


def _assert_no_url_secret(out: Path, *outputs: str) -> None:
    """No text projection and no file `verify` wrote repeats an unsanitized URL's path."""

    artifacts = [path.read_text(encoding="utf-8") for path in sorted(out.rglob("*")) if path.is_file()]
    assert artifacts
    for output in (*outputs, *artifacts):
        for secret in URL_SECRETS:
            assert secret not in output


def test_a_url_the_sanitizer_leaves_as_written_is_never_printed(tmp_path: Path) -> None:
    """Only a URL in the sanitized form prints; any other reads `url not shown`.

    The sanitizer rewrites only `http`, `https`, `ws`, `wss` and `sse` URLs to
    their scheme, host and a redacted path, and returns every other value as
    written. The grant publishes that value, so the text must not repeat it.
    """

    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {}}},
        {".mcp.json": {"mcpServers": {
            name: {"type": "http", "url": url} for name, url in UNSANITIZED_URLS.items()
        }}},
    )
    cells = [f"{name} (url not shown)" for name in sorted(UNSANITIZED_URLS)]

    text, block, summary, check = _projections(repo, tmp_path / "out")
    assert [
        " ".join(line.split()) for line in text.splitlines() if line.strip().endswith("(url not shown)")
    ] == cells
    _, payload = _diff(repo)
    assert sorted(row["after"] for row in payload["rows"]) == sorted(UNSANITIZED_URLS)
    for cell in cells:
        assert f"  — → {cell}" in block
        assert f"  — → {cell}" in check
    _assert_no_url_secret(tmp_path / "out", text, "\n".join(block), "\n".join(summary), "\n".join(check))


@pytest.mark.parametrize(
    ("transport", "endpoint", "launch"),
    [
        # The sanitized forms the engine publishes print as they are.
        ("url", "https://mcp.linear.app/<redacted-path>", "url https://mcp.linear.app/<redacted-path>"),
        ("url", "wss://mcp.example.com:8443", "url wss://mcp.example.com:8443"),
        ("url", "sse://mcp.example.com/", "url sse://mcp.example.com/"),
        ("url", "https://<invalid-host>/<redacted-path>", "url https://<invalid-host>/<redacted-path>"),
        # Every value the sanitizer returns as written does not.
        ("url", UNSANITIZED_URLS["hooks"], "url not shown"),
        ("url", UNSANITIZED_URLS["bare"], "url not shown"),
        ("url", UNSANITIZED_URLS["custom"], "url not shown"),
        ("url", "custom://mcp.example.com", "url not shown"),
        ("url", "<redacted-url>", "url not shown"),
        ("url", "https://mcp.example.com/v1", "url not shown"),
        ("url", "https://mcp.example.com/?token=abc", "url not shown"),
        ("url", " https://mcp.example.com", "url not shown"),
        # A `url` key with no string value leaves a command name as the endpoint.
        ("url", "npx", "url not shown"),
        ("stdio", "npx", "command name npx"),
    ],
)
def test_only_the_sanitized_url_form_is_printed(transport: str, endpoint: str, launch: str) -> None:
    from agents_shipgate.core.capability_diff_rows import _mcp_launch

    assert _mcp_launch({"transport": transport, "endpoint": endpoint}) == launch


def test_two_command_names_that_redact_alike_are_never_called_the_same(tmp_path: Path) -> None:
    other_token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {"gh": {"command": GITHUB_TOKEN}}}},
        {".mcp.json": {"mcpServers": {"gh": {"command": other_token}}}},
    )

    text, block, summary, check = _projections(repo, tmp_path / "out")
    assert "gh: command name changed (not shown)" in " ".join(text.split())
    for output in ("\n".join(block), "\n".join(check)):
        assert "  gh: command name changed (not shown)" in output
    for output in (text, "\n".join(block), "\n".join(summary), "\n".join(check)):
        assert GITHUB_TOKEN not in output and other_token not in output
        assert "no difference" not in output


def test_a_change_to_or_between_unprinted_urls_names_it_without_either_url(tmp_path: Path) -> None:
    """Two unprinted URLs never read `not shown → not shown`, nor as no difference."""

    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {
            "hooks": {"type": "http", "url": "https://hooks.example.com/v1/T0SECRETOLD"},
            "bare": {"type": "http", "url": "mcp.example.com/hooks/T0SECRETOLD/xyz"},
            "custom": {"type": "http", "url": UNSANITIZED_URLS["custom"], "alwaysAllow": ["read"]},
        }}},
        {".mcp.json": {"mcpServers": {
            "hooks": {"type": "http", "url": UNSANITIZED_URLS["hooks"]},
            "bare": {"type": "http", "url": UNSANITIZED_URLS["bare"]},
            "custom": {"type": "http", "url": UNSANITIZED_URLS["custom"], "alwaysAllow": ["read", "write"]},
        }}},
    )
    expected = [
        "bare: url changed (not shown)",
        "custom: no difference in the url as recorded, env key names or header key names; the "
        "change is in a detail this output does not show, such as the URL's query or another "
        "setting",
        "hooks: url https://hooks.example.com/<redacted-path> → not shown",
    ]

    text, block, summary, check = _projections(repo, tmp_path / "out")
    assert [
        " ".join(line.split()) for line in text.splitlines() if line.strip().startswith(("bare:", "custom:", "hooks:"))
    ] == expected
    for line in expected:
        assert f"  {line}" in block
        assert f"  {line}" in check
    _assert_no_url_secret(tmp_path / "out", text, "\n".join(block), "\n".join(summary), "\n".join(check))


def test_a_token_shaped_command_name_is_redacted_in_every_text_projection(
    tmp_path: Path,
) -> None:
    """The label redaction is the only guard: the grant's endpoint carries the name as read."""

    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {"gh": {"command": "npx"}}}},
        {".mcp.json": {"mcpServers": {
            "gh": {"command": GITHUB_TOKEN},
            "svc": {"command": f"/opt/bin/{GITHUB_TOKEN} --stdio"},
        }}},
    )

    text, _ = _diff(repo)
    assert "gh: command name npx → [REDACTED:github_token]" in text
    assert "svc (command name [REDACTED:github_token])" in text
    block, summary, _ = _verify(repo, tmp_path / "out")
    for output in (text, "\n".join(block), "\n".join(summary), "\n".join(_check(repo))):
        assert GITHUB_TOKEN not in output
        assert "[REDACTED:github_token]" in output


def test_a_token_shaped_env_key_name_is_redacted_and_long_key_lists_are_bounded(
    tmp_path: Path,
) -> None:
    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {}}},
        {".mcp.json": {"mcpServers": {
            "svc": {"command": "uvx", "env": {GITHUB_TOKEN: "x", "API_HOST": "y"}},
            "many": {"command": "uvx", "env": {f"KEY_{index}": "x" for index in range(7)}},
        }}},
    )

    text, _ = _diff(repo)
    assert GITHUB_TOKEN not in text
    assert "svc (command name uvx; env keys API_HOST [REDACTED:github_token])" in text
    assert "many (command name uvx; env keys KEY_0 KEY_1 KEY_2 KEY_3 KEY_4 and 2 more)" in text


# --- references, the question, and what stays unchanged ---------------------


def test_references_name_the_compared_commits_and_a_command_that_reproduces_them(
    tmp_path: Path,
) -> None:
    repo = _repository(
        tmp_path,
        {SETTINGS: {"permissions": {"allow": ["Bash(npm test *)"]}}},
        {SETTINGS: {"permissions": {"allow": ["Bash(npm *)"]}}},
    )
    base, head = _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "HEAD")
    command = f"agents-shipgate diff --base {base}"
    # One entry joins two rows; the control headline beside the PR comment's
    # question counts rows, so the question names both.
    question = "Review question: Does the team intend this declared capability change (from 2 rows)?"

    block, summary, verifier = _verify(repo, tmp_path / "commit")
    assert len(verifier["host_comparison"]["rows"]) == 2
    assert block[-3:] == [
        question,
        f"Compared: base {base[:8]} → head {head[:8]}, agents-shipgate {__version__}.",
        f"Reproduce: check out {head}, then run {command}",
    ]
    assert summary[-4:] == ["", *block[-3:-1], f"Reproduce: check out {head}, then run `{command}`"]
    # The printed command answers with the rows verify published.
    _, payload = _diff(repo)
    assert payload["base_commit"] == base
    assert _json_rows(payload["rows"]) == _json_rows(verifier["host_comparison"]["rows"])

    worktree, _, _ = _verify(repo, tmp_path / "worktree", head=False)
    assert worktree[-2:] == [
        f"Compared: base {base[:8]} → working tree at HEAD {head[:8]}, agents-shipgate {__version__}.",
        f"Reproduce in that working tree: {command}",
    ]
    text, _ = _diff(repo)
    assert text.rstrip().splitlines()[-3:] == [question, *worktree[-2:]]


@pytest.mark.parametrize(
    ("head_kind", "base_commit", "head_commit"),
    [
        ("provided_diff", "a" * 40, "b" * 40),
        ("worktree", None, "b" * 40),
        ("commit", "a" * 40, None),
    ],
)
def test_no_reference_is_printed_without_a_base_commit_and_a_readable_head(
    head_kind: str, base_commit: str | None, head_commit: str | None
) -> None:
    comparison = HostComparison(
        comparison_status="comparable",
        head_kind=head_kind,
        base_commit=base_commit,
        head_commit=head_commit,
    )
    assert comparison_reference_lines(comparison) == []
    assert comparison_reference_lines(comparison, markdown=True) == []


def test_no_change_and_incomparable_answers_ask_no_question(tmp_path: Path) -> None:
    """Their headlines are unchanged; what the run established follows them (#812)."""

    repo = _repository(
        tmp_path,
        {SETTINGS: {"permissions": {"allow": ["Bash(npm test *)"]}}, "README.md": "a\n"},
        {"README.md": "b\n"},
    )
    text, _ = _diff(repo)
    assert text.splitlines()[1:] == [
        "",
        "No static host-grant changes detected. No verdict is implied.",
        "",
        "What this run established:",
        "  compared with no change in what this entry reads: .claude/settings.json",
    ]
    block, summary, _ = _verify(repo, tmp_path / "quiet")
    expected = [
        "Repository-declared host capability changes:",
        "No static host-grant changes detected in the covered comparison. No verdict is implied.",
        "What this run established:",
        "- compared with no change in what this entry reads: .claude/settings.json",
    ]
    assert block == expected
    assert _plain(summary) == expected

    _write(repo, ".mcp.json", '{"mcpServers": {"docs": ')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "truncate")
    text, _ = _diff(repo)
    assert text.splitlines() == [
        "Cannot compare against main: head_inventory_incomplete",
        "This is an input limit, not a finding about the change. Nothing below is a claim that the change is safe.",
        "",
        "What this run established:",
        "  .mcp.json (claude-code): parse_failed in head, so the head inventory is incomplete",
    ]
    block, summary, _ = _verify(repo, tmp_path / "broken")
    assert block == [
        "Host capability comparison unavailable: head_inventory_incomplete",
        "What this run established:",
        "- .mcp.json (claude-code): parse_failed in head, so the head inventory is incomplete",
    ]
    assert summary[0] == "Host capability comparison unavailable: ` head_inventory_incomplete `"
    assert _plain(summary)[:3] == block
    assert not any("Review question" in line or "Reproduce" in line for line in summary)


def test_rows_read_back_from_json_render_their_published_values(tmp_path: Path) -> None:
    """The reviewer view is not published: a re-read comparison prints what it holds."""

    repo = _repository(
        tmp_path,
        {SETTINGS: {"permissions": {"allow": ["Bash(npm test *)"]}}},
        {SETTINGS: {"permissions": {"allow": ["Bash(npm *)"]}}},
    )
    _, _, verifier = _verify(repo, tmp_path / "out")
    reread = HostComparison.model_validate(verifier["host_comparison"])

    lines = host_comparison_lines(reread)
    assert lines[1:7] == [
        f"- ⚠ medium / added — {SUBJECT}",
        "  — → Bash(npm *)",
        "  runs without a prompt",
        f"- medium / removed — {SUBJECT}",
        "  Bash(npm test *) → —",
        "  removes a permission the agent previously had here",
    ]


def test_a_pair_split_by_a_caller_prints_each_row_alone(tmp_path: Path) -> None:
    from agents_shipgate.cli.verify.host_comparison import compare_host_refs

    repo = _repository(
        tmp_path,
        {SETTINGS: {"permissions": {"allow": ["Bash(npm test *)"]}}},
        {SETTINGS: {"permissions": {"allow": ["Bash(npm *)"]}}},
    )
    comparison = compare_host_refs(
        workspace=repo, base="main", head="HEAD", auto_base=False,
        config_relative=Path("shipgate.yaml"), out_dir=tmp_path / "out",
    )
    assert comparison is not None
    [joined] = review_changes(comparison.rows)
    assert (joined.direction, joined.rows) == ("widened", 2)
    [alone] = review_changes(comparison.rows[:1])
    assert (alone.direction, alone.before, alone.after, alone.rows) == (
        "added", "—", "allow: Bash(npm *)", 1,
    )
    # Equality and the published row ignore the view.
    reread = HostComparison.model_validate_json(comparison.model_dump_json())
    assert reread == comparison
