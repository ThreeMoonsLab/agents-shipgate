"""An MCP server URL's query reaches its change digest, never its published fields (#723)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_grants import (
    _redact_secret_values,
    _sha,
    host_audit_inventory,
    redacted_config_sha256,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def diff_rows(tmp_path: Path, before: dict, after: dict) -> list[dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"db": before}}), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"db": after}}), encoding="utf-8")
    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", "main", "--json"])
    payload = json.loads(result.stdout)
    assert payload["comparison_status"] == "comparable", payload
    return payload["rows"]


SUPABASE = "https://mcp.supabase.com/mcp?project_ref=abc&read_only=true"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (SUPABASE, "https://mcp.supabase.com/mcp?project_ref=abc"),
        (SUPABASE, SUPABASE + "&features=development"),
        ("https://mcp.supabase.com/mcp?project_ref=abc", "https://mcp.supabase.com/mcp?project_ref=xyz"),
        ("https://a.example.com/mcp", "https://b.example.com/mcp"),
    ],
)
def test_a_capability_change_carried_in_a_url_produces_a_row(tmp_path: Path, before: str, after: str) -> None:
    rows = diff_rows(tmp_path, {"type": "http", "url": before}, {"type": "http", "url": after})

    assert [row["direction"] for row in rows] in (["changed"], ["widened"]), rows
    assert rows[0]["before"] == rows[0]["after"] == "db"


def test_a_secret_query_value_alone_is_neither_digested_nor_a_row(tmp_path: Path) -> None:
    rows = diff_rows(
        tmp_path,
        {"type": "http", "url": "https://mcp.example.com/mcp?api_key=first"},
        {"type": "http", "url": "https://mcp.example.com/mcp?api_key=second"},
    )

    assert rows == []


def test_adding_a_secret_query_parameter_is_still_a_row(tmp_path: Path) -> None:
    rows = diff_rows(
        tmp_path,
        {"type": "http", "url": "https://mcp.example.com/mcp?project=a"},
        {"type": "http", "url": "https://mcp.example.com/mcp?project=a&api_key=secret"},
    )

    assert len(rows) == 1


def test_a_url_in_env_stays_out_of_the_digest(tmp_path: Path) -> None:
    rows = diff_rows(
        tmp_path,
        {"command": "server", "env": {"API_BASE": "https://api.example.com/v1?key=a"}},
        {"command": "server", "env": {"API_BASE": "https://api.example.com/v2?key=b"}},
    )

    assert rows == []


def test_reordering_keys_is_not_a_change(tmp_path: Path) -> None:
    rows = diff_rows(
        tmp_path,
        {"type": "http", "url": SUPABASE},
        {"url": SUPABASE, "type": "http"},
    )

    assert rows == []


@pytest.mark.parametrize(
    "config",
    [
        {"command": "npx", "args": ["-y", "server"]},
        {"type": "http", "url": "https://mcp.example.com"},
        {"type": "http", "url": "https://mcp.example.com/"},
        {"type": "http", "url": "https://mcp.example.com/services/T00/B00/SECRETPATH"},
    ],
)
def test_a_server_without_a_url_query_keeps_its_earlier_digest(config: dict) -> None:
    """Upgrading must not churn every saved baseline: only URL servers with a
    query are digested differently."""

    assert redacted_config_sha256(config) == _sha(_redact_secret_values(config))


def test_nothing_published_carries_the_path_query_or_secret(tmp_path: Path) -> None:
    repo = tmp_path / "published"
    repo.mkdir()
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"db": {
        "type": "http",
        "url": "https://mcp.supabase.com/private-path?project_ref=abc&read_only=true&api_key=TOPSECRET",
    }}}), encoding="utf-8")

    inventory = json.dumps(host_audit_inventory(repo))

    for leaked in ("private-path", "project_ref", "read_only", "TOPSECRET", "api_key"):
        assert leaked not in inventory, leaked
    assert "https://mcp.supabase.com/<redacted-path>" in inventory


def test_a_path_rotation_stays_quiet_because_a_path_can_be_the_secret(tmp_path: Path) -> None:
    """The deliberate limit: `/read` → `/admin` is unseen, because a webhook path
    rotation must stay quiet and a digest cannot tell the two apart."""

    rows = diff_rows(
        tmp_path,
        {"type": "http", "url": "https://hooks.example.com/services/T00/B00/FIRST"},
        {"type": "http", "url": "https://hooks.example.com/services/T00/B00/SECOND"},
    )

    assert rows == []

