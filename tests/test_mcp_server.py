from __future__ import annotations

import shutil
from pathlib import Path

from agents_shipgate.mcp_server import (
    shipgate_capabilities,
    shipgate_explain,
    shipgate_preflight,
)


def _snapshot(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )


def test_mcp_preflight_handler_is_read_only(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)
    before = _snapshot(workspace)

    payload = shipgate_preflight(
        workspace=str(workspace),
        changed_files=["shipgate.yaml"],
        diff_text=(
            "diff --git a/.cursor/rules/agents-shipgate.mdc "
            "b/.cursor/rules/agents-shipgate.mdc\n"
            "--- a/.cursor/rules/agents-shipgate.mdc\n"
            "+++ b/.cursor/rules/agents-shipgate.mdc\n"
        ),
    )

    assert payload["preflight_schema_version"] == "0.1"
    assert payload["requires_human_review"] is True
    assert {
        touch["path"] for touch in payload["protected_surface_touches"]
    } >= {"shipgate.yaml", ".cursor/rules/agents-shipgate.mdc"}
    assert _snapshot(workspace) == before


def test_mcp_explain_handler_returns_check_metadata() -> None:
    payload = shipgate_explain(
        check_id="SHIP-POLICY-APPROVAL-MISSING",
        no_plugins=True,
    )

    assert payload["id"] == "SHIP-POLICY-APPROVAL-MISSING"
    assert payload["category"] == "policy"


def test_mcp_capabilities_handler_does_not_write_reports(tmp_path: Path) -> None:
    workspace = tmp_path / "wk"
    shutil.copytree("samples/clean_read_only_agent", workspace)
    before = _snapshot(workspace)

    payload = shipgate_capabilities(
        config=str(workspace / "shipgate.yaml"),
        no_plugins=True,
    )

    assert payload["capability_lock_schema_version"] == "0.2"
    assert _snapshot(workspace) == before
