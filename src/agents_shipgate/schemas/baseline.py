from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.surfaces import ActionSurfaceFacts, ToolSurfaceFacts

BASELINE_SCHEMA_VERSION = "0.8"
# v0.6 adds optional `provenance.owner` — the human who accepted the
# debt — settable via `baseline save --owner/--reason/--expires`
# (v0.5 documented reviewer-set `reason`/`expires` but offered no CLI
# path, and hand-editing the file breaks the integrity hash).
# v0.5 added self-describing entry provenance. Older versions
# (0.2/0.3/0.4) load with `BaselineFinding.provenance = None`; the
# integrity check then flags them as `SHIP-BASELINE-ENTRY-STALE`
# (kind="legacy_no_provenance") in warn/strict modes. Re-saving with
# `baseline save` upgrades the file to the current version and stamps
# provenance on every entry.


class BaselineProvenance(BaseModel):
    """Self-describing record of when and why a baseline entry was added.

    Written by `agents-shipgate baseline save` for every new fingerprint
    in the baseline. Existing fingerprints keep their original provenance
    on re-save; only newly-added ones get a fresh `recorded_at` / `run_id`.

    `owner`, `reason`, and `expires` are reviewer-controlled exception
    metadata, set via `baseline save --owner/--reason/--expires` — never
    inferred (declared human authority cannot be synthesized, matching
    the `human_ack` contract). When `expires` is set, the integrity check
    emits `SHIP-BASELINE-ENTRY-EXPIRED` past that date.
    """

    model_config = ConfigDict(extra="forbid")

    scanner_version: str
    run_id: str
    recorded_at: str
    owner: str | None = None
    reason: str | None = None
    expires: date | None = None


class BaselineFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    check_id: str
    tool_id: str | None = None
    tool_name: str | None = None
    fingerprint_version: Literal["1", "2"] = "1"
    severity: Severity
    title: str
    support_hash: str | None = None
    # v0.5 additive: when None, the entry pre-dates the v0.5 provenance
    # contract (loaded from 0.2/0.3/0.4) or was constructed by a test
    # helper. Re-saving via `baseline save` populates it.
    provenance: BaselineProvenance | None = None


class BaselineFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8"] = BASELINE_SCHEMA_VERSION
    project: dict[str, object] = Field(default_factory=dict)
    agent: dict[str, object] = Field(default_factory=dict)
    created_at: str
    source_report_run_id: str
    findings: list[BaselineFinding] = Field(default_factory=list)
    tool_surface_facts: ToolSurfaceFacts | None = None
    action_surface_facts: ActionSurfaceFacts | None = None
    notes: list[str] = Field(default_factory=list)
