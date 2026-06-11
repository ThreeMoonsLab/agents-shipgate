"""Downstream local agent contract written by ``init --agent-instructions=default``."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from agents_shipgate import __version__
from agents_shipgate.schemas.contract import (
    ARTIFACTS,
    COMMANDS,
    CONTRACT_VERSION,
    DEFAULT_PATHS,
    DO_NOT_AUTO_ASSERT,
    GATING_SIGNAL,
    MERGE_VERDICTS,
    RELEASE_DECISIONS,
    VERIFIER_READ_ORDER,
)

LOCAL_CONTRACT_SCHEMA_VERSION = "1"
LOCAL_CONTRACT_RELATIVE_PATH = ".shipgate/agent-contract.json"


class LocalAgentContract(BaseModel):
    """Minimal local contract for cold-start coding agents in downstream repos."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    agents_shipgate_version: str
    contract_version: str
    default_paths: dict[str, str]
    commands: dict[str, str]
    artifacts: dict[str, str]
    verifier_read_order: list[str]
    gating_signal: str
    merge_verdicts: list[str]
    release_decisions: list[str]
    do_not_auto_assert: list[str]


def build_local_agent_contract() -> LocalAgentContract:
    """Build the local downstream contract from runtime contract constants."""

    return LocalAgentContract(
        schema_version=LOCAL_CONTRACT_SCHEMA_VERSION,
        agents_shipgate_version=__version__,
        contract_version=CONTRACT_VERSION,
        default_paths=dict(DEFAULT_PATHS),
        commands=dict(COMMANDS),
        artifacts=dict(ARTIFACTS),
        verifier_read_order=list(VERIFIER_READ_ORDER),
        gating_signal=GATING_SIGNAL,
        merge_verdicts=list(MERGE_VERDICTS),
        release_decisions=list(RELEASE_DECISIONS),
        do_not_auto_assert=list(DO_NOT_AUTO_ASSERT),
    )


def render_local_agent_contract() -> str:
    """Render the local contract as stable pretty JSON."""

    payload = build_local_agent_contract().model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


__all__ = [
    "LOCAL_CONTRACT_RELATIVE_PATH",
    "LOCAL_CONTRACT_SCHEMA_VERSION",
    "LocalAgentContract",
    "build_local_agent_contract",
    "render_local_agent_contract",
]
