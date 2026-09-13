"""#731: `.vscode/mcp.json` is a supported host surface.

It was `experimental`, so any repository with the file refused every host
comparison whenever the file changed: 0 of 6 such PRs compared in #659. The
owner decision on #731 promotes the documented shape. The server set is compared
as for `.mcp.json`. `sandbox` and a server's `sandboxEnabled` are sandbox
grants. An `${input:…}` reference contributes its name, never a value. `envFile`
is a non-blocking limit, and any other top-level key keeps coverage partial.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents_shipgate.cli.host_audit import host_audit_inventory


def _inventory(root: Path, document: dict) -> dict:
    (root / ".vscode").mkdir(parents=True, exist_ok=True)
    (root / ".vscode" / "mcp.json").write_text(json.dumps(document), encoding="utf-8")
    return host_audit_inventory(root)


def _coverage(inventory: dict) -> dict:
    return next(item for item in inventory["host_coverage"] if item["host"] == "vscode")


def _grants(inventory: dict, kind: str) -> list[dict]:
    return [grant for grant in inventory["grants"] if grant["host"] == "vscode" and grant["kind"] == kind]


def test_the_documented_shape_is_complete_coverage(tmp_path: Path) -> None:
    inventory = _inventory(
        tmp_path,
        {
            "inputs": [{"type": "promptString", "id": "key", "password": True}],
            "servers": {
                "docs": {"type": "stdio", "command": "docs", "env": {"KEY": "${input:key}"}},
                "remote": {"type": "http", "url": "https://mcp.example.com/mcp"},
            },
        },
    )

    assert _coverage(inventory)["status"] == "complete"
    assert sorted(grant["server"] for grant in _grants(inventory, "mcp_server")) == ["docs", "remote"]


def test_sandbox_settings_are_sandbox_grants(tmp_path: Path) -> None:
    inventory = _inventory(
        tmp_path,
        {
            "sandbox": {"network": {"allowedDomains": ["api.example.com"]}},
            "servers": {"docs": {"command": "docs", "sandboxEnabled": False}},
        },
    )

    assert sorted(grant["setting"] for grant in _grants(inventory, "sandbox")) == [
        "sandbox.network",
        "servers.docs.sandboxEnabled",
    ]


def test_toggling_sandbox_enabled_leaves_the_server_digest_alone(tmp_path: Path) -> None:
    """One edit, one grant: the sandbox row, not also a changed server."""

    def server_digest(name: str, enabled: bool) -> str:
        inventory = _inventory(tmp_path / name, {"servers": {"docs": {"command": "docs", "sandboxEnabled": enabled}}})
        [server] = _grants(inventory, "mcp_server")
        return server["config_sha256"]

    assert server_digest("on", True) == server_digest("off", False)


def test_an_input_reference_name_enters_the_digest_and_a_value_never_does(tmp_path: Path) -> None:
    def server_digest(name: str, env: dict) -> str:
        inventory = _inventory(tmp_path / name, {"servers": {"docs": {"command": "docs", "env": env}}})
        [server] = _grants(inventory, "mcp_server")
        return server["config_sha256"]

    literal = server_digest("literal", {"KEY": "one"})
    other_literal = server_digest("other-literal", {"KEY": "two"})
    reference = server_digest("reference", {"KEY": "${input:key}"})
    other_reference = server_digest("other-reference", {"KEY": "${input:other}"})

    assert literal == other_literal
    assert reference != other_reference
    assert reference != literal


def test_env_file_is_a_non_blocking_limit(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path, {"servers": {"docs": {"command": "docs", "envFile": "${workspaceFolder}/.env"}}})

    issues = [item for item in inventory["issues"] if item["host"] == "vscode"]
    assert [(item["kind"], item["blocking"]) for item in issues] == [("unsupported", False)]
    assert _coverage(inventory)["status"] == "complete"


def test_an_unmodeled_top_level_key_keeps_coverage_partial(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path, {"servers": {}, "extensions": {"recommended": True}})

    assert _coverage(inventory)["status"] == "partial"
