from __future__ import annotations

import json

from agents_shipgate import __version__
from agents_shipgate.cli.discovery.local_contract import (
    LOCAL_CONTRACT_RELATIVE_PATH,
    LOCAL_CONTRACT_SCHEMA_VERSION,
    build_local_agent_contract,
    render_local_agent_contract,
)
from agents_shipgate.schemas.contract import CONTRACT_VERSION, GATING_SIGNAL


def test_local_agent_contract_is_minimal_agent_operational_payload() -> None:
    payload = build_local_agent_contract().model_dump(mode="json")

    assert list(payload) == [
        "schema_version",
        "agents_shipgate_version",
        "contract_version",
        "default_paths",
        "commands",
        "artifacts",
        "verifier_read_order",
        "gating_signal",
        "merge_verdicts",
        "release_decisions",
        "do_not_auto_assert",
    ]
    assert payload["schema_version"] == LOCAL_CONTRACT_SCHEMA_VERSION
    assert payload["agents_shipgate_version"] == __version__
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["default_paths"]["local_contract"] == LOCAL_CONTRACT_RELATIVE_PATH
    assert payload["commands"]["install_agent_workflow"] == (
        "agents-shipgate init --workspace . --write --ci --agent-instructions=default --json"
    )
    assert payload["artifacts"]["verifier"] == "agents-shipgate-reports/verifier.json"
    assert payload["verifier_read_order"][0] == "merge_verdict"
    assert payload["gating_signal"] == GATING_SIGNAL
    assert "blocked" in payload["merge_verdicts"]
    assert "passed" in payload["release_decisions"]
    assert "approval" in payload["do_not_auto_assert"]


def test_local_agent_contract_renders_stable_pretty_json() -> None:
    rendered = render_local_agent_contract()

    assert rendered.endswith("\n")
    parsed = json.loads(rendered)
    assert parsed == build_local_agent_contract().model_dump(mode="json")
