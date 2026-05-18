"""Runtime contract metadata for local agent consumers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from agents_shipgate import __version__
from agents_shipgate.schemas.packet import EvidencePacket
from agents_shipgate.schemas.report import ReadinessReport

CONTRACT_VERSION: Literal["1"] = "1"
GATING_SIGNAL: Literal["release_decision.decision"] = "release_decision.decision"
# Adding `gating_signal_values` would be a `contract_version: "2"` change.
MANUAL_REVIEW_SIGNALS: tuple[str, ...] = (
    "release_decision.review_items",
    # v0.17: per-finding decision audit. Reviewers triaging
    # `release_decision.review_items` use the corresponding
    # `contribution_rules[]` row to see WHY each item was classified
    # as a review item (`policy_baseline_accepted`,
    # `severity_baseline_accepted`, or `review_required`).
    "release_decision.contribution_rules",
    "findings[].requires_human_review",
    "findings[].blocks_release",
    "summary.human_review_recommended",
    "action_surface_diff",
    "codex_plugin_surface",
    "packet.capability_intent.divergence_findings",
    "packet.approval_coverage.gap_findings",
    "packet.idempotency_risk.gap_findings",
    "packet.scope_coverage.gap_findings",
    "packet.human_in_the_loop.trace_findings",
    "packet.dynamic_scenarios.scenarios",
)


class ContractPayload(BaseModel):
    """Stable JSON payload emitted by ``agents-shipgate contract --json``."""

    # New fields must be deliberate contract changes with a version bump.
    model_config = ConfigDict(extra="forbid")

    contract_version: str
    cli_version: str
    report_schema_version: str
    packet_schema_version: str
    gating_signal: str
    manual_review_signals: list[str]


def build_contract_payload() -> ContractPayload:
    """Build the local CLI contract from runtime constants."""

    report_schema_version = ReadinessReport.model_fields[
        "report_schema_version"
    ].default
    packet_schema_version = EvidencePacket.model_fields[
        "packet_schema_version"
    ].default
    return ContractPayload(
        contract_version=CONTRACT_VERSION,
        cli_version=__version__,
        report_schema_version=str(report_schema_version),
        packet_schema_version=str(packet_schema_version),
        gating_signal=GATING_SIGNAL,
        manual_review_signals=list(MANUAL_REVIEW_SIGNALS),
    )


__all__ = [
    "CONTRACT_VERSION",
    "GATING_SIGNAL",
    "MANUAL_REVIEW_SIGNALS",
    "ContractPayload",
    "build_contract_payload",
]
