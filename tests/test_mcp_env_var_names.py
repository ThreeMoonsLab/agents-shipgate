"""#795: an MCP row names which `env_vars` entries changed, and never a value.

Replaying openai/codex-security#1281, `plugins/codex-security/.mcp.json` added
`CODEX_SECURITY_PLUGIN_ROOT` to a server's `env_vars` array and changed
nothing else. The row read `no difference in the command name …, launch
arguments, env key names or header key names; the change is in a detail this
output does not show`, because the grant published the keys of the `env` map
and not the names an `env_vars` list passes through. The grant now publishes
those names (host-grants `0.9`, extended in place) and the row names the ones
that moved, beside the existing `env keys` and `header keys`.

Display only: no row, direction, `expands`, severity, expansion signal,
digest, baseline or `check` decision moves. The `env` map's values are
redacted whole in `config_sha256`'s input, so rotating one is still no row,
and no value is published or printed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agents_shipgate.core.capability_diff_rows import (
    _mcp_change,
    capability_diff_rows,
    review_changes,
)
from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    _mcp_env_var_names,
    _mcp_grants,
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
    compared_grant,
    host_grants_sha256,
)
from agents_shipgate.schemas.host_grants import HostMcpServerBaselineGrantV9, HostMcpServerGrantV9
from tests.test_hook_mcp_detail_fields import _boundary, _every_route
from tests.test_host_diff_review_changes import (
    _check,
    _diff,
    _git,
    _repository,
    _table_entry,
    _verify,
    _write,
)

PLUGIN_MCP = "plugins/codex-security/.mcp.json"
HEADER = f"high changed claude-code {PLUGIN_MCP}"
SERVER = {
    "command": "launch_codex_security_mcp",
    "args": ["--stdio"],
    "env": {"CODEX_HOME": "canary-home-value"},
}
SHOWN = (
    "no difference in the command name launch_codex_security_mcp, launch arguments, "
    "env key names, env_vars names or header key names; the change is in a detail this output "
    "does not show, such as the command's path or another setting"
)


def _mcp(**fields: object) -> dict:
    return {"mcpServers": {"codex-security": {**SERVER, **fields}}}


def _rows(payload: dict) -> list[dict]:
    """The published rows, and the presentation of each but its text."""

    return [
        *payload["rows"],
        *({key: value for key, value in entry.items() if key != "change"} for entry in payload["review"]["changes"]),
    ]


# --- the reproduction, on every route ---------------------------------------


def test_the_reproduction_names_the_added_env_var_on_every_route(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path, {PLUGIN_MCP: _mcp()}, {PLUGIN_MCP: _mcp(env_vars=["CODEX_SECURITY_PLUGIN_ROOT"])}
    )
    change = "codex-security: env_vars names +CODEX_SECURITY_PLUGIN_ROOT"

    text, payload = _diff(repo)
    assert _table_entry(text, HEADER) == [
        HEADER, change, "MCP edit; authority direction is unknown",
    ]
    _every_route(repo, tmp_path / "out", change)
    # `check --diff` reads the same rows from a patch.
    patch = tmp_path / "change.diff"
    patch.write_text(_git(repo, "diff", "main", "HEAD") + "\n", encoding="utf-8")
    assert f"  {change}" in _check(repo, "--diff", str(patch))
    [row] = payload["rows"]
    assert (row["direction"], row["expands"], row["severity"]) == ("changed", False, "high")
    assert "this output does not show" not in text
    assert "canary-home-value" not in text + json.dumps(payload)


@pytest.mark.parametrize(
    "base,head,change",
    [
        pytest.param(["A"], ["A", "B"], "env_vars names +B", id="added"),
        pytest.param(["A", "B"], ["A"], "env_vars names -B", id="removed"),
        pytest.param(["A", "B"], ["B", "C"], "env_vars names +C -A", id="both"),
        pytest.param(["A", "B"], ["B", "A"], "env_vars names in a different order", id="reordered"),
        pytest.param(["A"], ["A", "A"], "env_vars names listed a different number of times", id="repeated"),
        pytest.param(
            ["NPM_TOKEN"], ["NPM_TOKEN", "GITHUB_TOKEN"], "env_vars names +GITHUB_TOKEN",
            id="a-name-with-a-credential-word",
        ),
        pytest.param(
            [f"N{index}" for index in range(8)], [f"N{index}" for index in range(2, 10)],
            "env_vars names +N8 +N9 -N0 -N1", id="several",
        ),
        pytest.param(
            [], [f"N{index}" for index in range(7)],
            "env_vars names +N0 +N1 +N2 +N3 +N4 and 2 more", id="bounded",
        ),
    ],
)
def test_each_change_to_the_list_is_named(base: list, head: list, change: str) -> None:
    assert _mcp_change("docs", _grant(env_vars=base), _grant(env_vars=head)) == f"docs: {change}"


def test_a_reorder_is_a_row_on_every_route(tmp_path: Path) -> None:
    """`config_sha256` reads the list in order, so a reorder is a row, and it says only that."""

    repo = _repository(
        tmp_path, {PLUGIN_MCP: _mcp(env_vars=["A", "B"])}, {PLUGIN_MCP: _mcp(env_vars=["B", "A"])}
    )
    _every_route(repo, tmp_path / "out", "codex-security: env_vars names in a different order")


def test_the_list_is_named_beside_the_env_map_and_the_headers(tmp_path: Path) -> None:
    base = {"mcpServers": {"remote": {
        "url": "https://mcp.example.com/path", "env": {"DEBUG": "1"}, "headers": {"X-Old": "o"},
        "env_vars": ["A"],
    }}}
    head = {"mcpServers": {"remote": {
        "url": "https://mcp.example.com/path", "env": {"API_BASE": "1"}, "headers": {"X-New": "n"},
        "env_vars": ["B"],
    }}}
    repo = _repository(tmp_path, {".mcp.json": base}, {".mcp.json": head})
    _every_route(
        repo, tmp_path / "out",
        "remote: env keys +API_BASE -DEBUG; env_vars names +B -A; header keys +X-New -X-Old",
    )


def test_an_added_and_a_removed_server_list_the_names_they_pass_through(tmp_path: Path) -> None:
    (tmp_path / "added").mkdir()
    (tmp_path / "removed").mkdir()
    server = {"command": "npx", "args": ["-y", "example-mcp-server@2.0.0"], "env_vars": ["A", "B"]}
    added = _repository(tmp_path / "added", {".mcp.json": {"mcpServers": {}}}, {".mcp.json": {"mcpServers": {"docs": server}}})
    text, payload = _diff(added)
    assert "docs (command name npx; package example-mcp-server@2.0.0; env_vars names A B)" in " ".join(text.split())
    cell = "docs (command name npx; package example-mcp-server@2.0.0; env_vars names A B)"
    assert payload["review"]["changes"][0]["after"] == cell
    removed = _repository(
        tmp_path / "removed", {".mcp.json": {"mcpServers": {"docs": server}}}, {".mcp.json": {"mcpServers": {}}}
    )
    assert _diff(removed)[1]["review"]["changes"][0]["before"] == cell


def test_a_codex_config_names_its_env_vars(tmp_path: Path) -> None:
    def config(names: str) -> str:
        return f'[mcp_servers.docs]\ncommand = "npx"\nargs = ["-y", "example-mcp-server@1.2.3"]\nenv_vars = {names}\n'

    repo = _repository(
        tmp_path, {".codex/config.toml": config('["A"]')}, {".codex/config.toml": config('["A", "B"]')}
    )
    text, payload = _diff(repo)
    assert [entry["change"] for entry in payload["review"]["changes"]] == ["docs: env_vars names +B"]
    assert "env_vars names +B" in text


# --- what stays as it was ----------------------------------------------------


def test_nothing_but_the_text_moves(tmp_path: Path) -> None:
    """A head the names cannot describe is the same row and `check` result, with the old wording."""

    (tmp_path / "named").mkdir()
    (tmp_path / "unnamed").mkdir()
    named = _repository(tmp_path / "named", {PLUGIN_MCP: _mcp()}, {PLUGIN_MCP: _mcp(env_vars=["NAME"])})
    # An object is not a plain name, so it is not published; the list still changes the digest.
    unnamed = _repository(
        tmp_path / "unnamed", {PLUGIN_MCP: _mcp()}, {PLUGIN_MCP: _mcp(env_vars=[{"name": "NAME"}])}
    )
    named_payload, unnamed_payload = _diff(named)[1], _diff(unnamed)[1]
    assert _rows(named_payload) == _rows(unnamed_payload)
    assert named_payload["review"]["changes"][0]["change"] == "codex-security: env_vars names +NAME"
    assert unnamed_payload["review"]["changes"][0]["change"].startswith("codex-security: no difference in")

    def boundary(repo: Path) -> str:
        """`check`'s whole result, with the one repository's path and head commit set aside."""

        return re.sub(
            r"boundary_[0-9a-f]{24}", "boundary_<id>",
            json.dumps(_boundary(repo), sort_keys=True)
            .replace(str(repo), "<repo>")
            .replace(_git(repo, "rev-parse", "HEAD"), "<head>"),
        )

    assert boundary(named) == boundary(unnamed)


def test_a_value_the_digest_redacts_is_still_no_row(tmp_path: Path) -> None:
    """`env` values are redacted whole in `config_sha256`'s input: a rotation is no change."""

    repo = _repository(
        tmp_path,
        {PLUGIN_MCP: _mcp(env={"API_TOKEN": "canary-first-value"})},
        {PLUGIN_MCP: _mcp(env={"API_TOKEN": "canary-second-value"})},
    )
    text, payload = _diff(repo)
    assert payload["rows"] == [] and payload["review"]["changes"] == []
    assert "canary" not in text + json.dumps(payload)


def test_the_same_env_map_in_another_order_is_no_row(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {PLUGIN_MCP: _mcp(env={"A": "1", "B": "2"}, env_vars=["X", "Y"])},
        {PLUGIN_MCP: _mcp(env={"B": "2", "A": "1"}, env_vars=["X", "Y"])},
    )
    assert _diff(repo)[1]["rows"] == []


def test_an_unpublished_entry_keeps_the_wording_for_what_is_not_shown(tmp_path: Path) -> None:
    """A `NAME=value` entry, an object or a token-shaped name is never published, and the row says so."""

    token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    entries = [f"API_BASE=canary-{token}", {"name": "OBJ", "source": "canary-object"}, token, "with space"]
    repo = _repository(
        tmp_path, {PLUGIN_MCP: _mcp(env_vars=["A"])}, {PLUGIN_MCP: _mcp(env_vars=["A", *entries])}
    )
    text, payload = _diff(repo)
    [entry] = payload["review"]["changes"]
    assert entry["change"] == f"codex-security: {SHOWN}"
    block, _summary, verifier = _verify(repo, tmp_path / "out")
    outputs = [
        text, json.dumps(payload), "\n".join(block), json.dumps(verifier),
        (tmp_path / "out" / "pr-comment.md").read_text(encoding="utf-8"),
        "\n".join(_check(repo)), json.dumps(_boundary(repo)),
    ]
    for output in outputs:
        assert "canary" not in output and token not in output and "OBJ" not in output


def test_the_published_names_are_plain_names_the_digest_keeps() -> None:
    token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    names = [
        "PLAIN", "_UNDER", "lower_case", "GITHUB_TOKEN", "A" * 80,
        "A" * 81, "1LEADING_DIGIT", "NAME=value", "has-dash", "", token, 7, None, {"name": "OBJ"},
    ]
    assert _mcp_env_var_names({"env_vars": names}) == [
        "PLAIN", "_UNDER", "lower_case", "GITHUB_TOKEN", "A" * 80,
    ]
    # The entry a credential word before it makes the digest redact is not published either.
    assert _mcp_env_var_names({"env_vars": ["A", "api_key", "B", "C"]}) == ["A", "api_key", "C"]
    for declared in (None, "NAME", {"NAME": "value"}, 3):
        assert _mcp_env_var_names({"env_vars": declared}) == []
    assert _mcp_env_var_names({}) == []


def test_a_credential_word_before_a_name_hides_a_change_the_digest_never_saw(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {PLUGIN_MCP: _mcp(env_vars=["API_KEY", "ONE"])},
        {PLUGIN_MCP: _mcp(env_vars=["API_KEY", "TWO"])},
    )
    assert _diff(repo)[1]["rows"] == []


# --- the grant ----------------------------------------------------------------


def _grant(**fields: object) -> dict:
    (grant,) = _mcp_grants(
        {"mcpServers": {"docs": {**SERVER, **fields}}}, host="claude-code", scope="repository", source=".mcp.json"
    )
    return grant


def test_the_field_is_display_only() -> None:
    plain, named = _grant(), _grant(env_vars=["A", "B"])
    assert plain["env_var_names"] == [] and named["env_var_names"] == ["A", "B"]
    HostMcpServerGrantV9.model_validate(named)
    # The list is in the digest's input, so a change to it is still a row; the published names are not compared.
    assert plain["config_sha256"] != named["config_sha256"]
    assert compared_grant(named) == compared_grant({**named, "env_var_names": ["Z"]})
    assert "env_var_names" not in compared_grant(named)

    def inventory(grant: dict) -> dict:
        return {"grants": [grant]}

    other = {**named, "env_var_names": ["Z"]}
    assert host_grants_sha256(inventory(named)) == host_grants_sha256(inventory(other))


def test_a_saved_baseline_holds_no_names(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, ".mcp.json", _mcp(env_vars=["A", "B"]))
    inventory = build_host_boundary_snapshot(root, cache=HostStaticParseCache()).inventory
    (grant,) = [grant for grant in inventory["grants"] if grant["kind"] == "mcp_server"]
    assert grant["env_var_names"] == ["A", "B"]
    baseline = build_host_grants_baseline(inventory)
    (saved,) = [grant for grant in baseline["inventory"]["grants"] if grant["kind"] == "mcp_server"]
    assert "env_var_names" not in saved
    HostMcpServerBaselineGrantV9.model_validate(saved)
    # The names are not in the digest: another list of them gives the same one.
    other = {
        **inventory,
        "grants": [{**g, "env_var_names": ["Z"]} if g["kind"] == "mcp_server" else g for g in inventory["grants"]],
    }
    assert build_host_grants_baseline(other)["inventory_sha256"] == baseline["inventory_sha256"]
    assert host_grants_sha256(other) == host_grants_sha256(inventory)

    drift = build_host_drift_payload(baseline=baseline, inventory=inventory, baseline_file="b.json")
    assert (drift["comparison_status"], drift["has_drift"], drift["changes"]) == ("comparable", False, [])
    # A change is still a row; the saved side names no list it cannot show.
    _write(root, ".mcp.json", _mcp(env_vars=["A", "C"]))
    changed = build_host_drift_payload(
        baseline=baseline, inventory=build_host_boundary_snapshot(root, cache=HostStaticParseCache()).inventory,
        baseline_file="b.json",
    )
    [row] = capability_diff_rows(changed)
    [presented] = review_changes([row])
    assert "env_vars names" not in (presented.change or "")


def test_a_reading_that_publishes_no_names_claims_nothing_about_them() -> None:
    base, head = _grant(env_vars=["A"]), _grant(env_vars=["A", "B"])
    legacy = {key: value for key, value in base.items() if key != "env_var_names"}
    # A baseline's side has none of the display members; the other side's names are not read as a change.
    assert "env_vars names" not in (_mcp_change("docs", legacy, head) or "")
    assert _mcp_change("docs", base, head) == "docs: env_vars names +B"
    assert _mcp_change("docs", base, base) == f"docs: {SHOWN}"
    # Neither side declaring any leaves the sentence as it was.
    unchanged = _mcp_change("docs", _grant(), _grant())
    assert unchanged is not None and "env_vars names" not in unchanged
