"""Control maps and the evidence extract ``registry report --control-map`` writes.

A **control map** is data: for each control activity of a framework, which
ledger row kinds speak to it, what they show in one line, and which gap rules
apply. Nothing in the engine names a framework; ``docs/control-maps/aiuc-1.yaml``
is the first map, and a team can write its own.

The **control evidence** extract is a derived, read-only view over the registry
ledger for one audit window: per activity, the rows cited, their counts, and
every gap the map's rules find, plus the extract of exactly those rows so each
count can be recomputed. It states rows, counts and gaps. It never says a
control is met: the assessor decides, and ``release_decision.decision``
remains the only release gate (#1121).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.registry import (
    RegistryDriftGapV1,
    RegistryRowKind,
    RegistrySkippedRowV1,
)

CONTROL_MAP_SCHEMA_VERSION = "0.1"
CONTROL_EVIDENCE_SCHEMA_VERSION = "0.1"
CONTROL_EVIDENCE_FILENAME = "control-evidence.json"
CONTROL_EVIDENCE_MARKDOWN_FILENAME = "control-evidence.md"
LEDGER_EXTRACT_FILENAME = "ledger-extract.jsonl"
#: The map ``--out`` ships beside the extract, its bytes unchanged, so the
#: recompute command runs from that directory alone.
CONTROL_MAP_COPY_FILENAME = "control-map.yaml"

#: The gap rules the engine implements. Closed: a map names rules, it does not
#: define them, so two maps that name one rule mean one computation.
ControlGapRule = Literal[
    # Generic.
    "no_rows_in_window",
    # Drift observations (registry 0.6).
    "observation_interval",
    "observation_incomparable",
    "inventory_issues",
    "capability_comparison_absent",
    # Attestations.
    "definition_digests_absent",
    "approval_not_supplied",
    "approval_not_approved",
]
CONTROL_GAP_RULES: tuple[str, ...] = (
    "no_rows_in_window",
    "observation_interval",
    "observation_incomparable",
    "inventory_issues",
    "capability_comparison_absent",
    "definition_digests_absent",
    "approval_not_supplied",
    "approval_not_approved",
)
#: Which row kind each rule reads. A map that applies a rule to the other kind
#: is refused rather than reported as a gap that cannot occur.
GAP_RULE_ROW_KIND: dict[str, RegistryRowKind | None] = {
    "no_rows_in_window": None,
    "observation_interval": "drift_observation",
    "observation_incomparable": "drift_observation",
    "inventory_issues": "drift_observation",
    "capability_comparison_absent": "drift_observation",
    "definition_digests_absent": "attestation",
    "approval_not_supplied": "attestation",
    "approval_not_approved": "attestation",
}

ControlGapStatus = Literal["found", "none", "not_checked"]


class ControlMapEvidence(BaseModel):
    """One kind of ledger row cited for an activity."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    row_kind: RegistryRowKind
    gaps: list[ControlGapRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rules_read_this_row_kind(self) -> ControlMapEvidence:
        if len(set(self.gaps)) != len(self.gaps):
            raise ValueError(f"evidence {self.label!r} names a gap rule twice")
        for rule in self.gaps:
            kind = GAP_RULE_ROW_KIND[rule]
            if kind is not None and kind != self.row_kind:
                raise ValueError(
                    f"gap rule {rule!r} reads {kind} rows; evidence {self.label!r} "
                    f"cites {self.row_kind} rows"
                )
        return self


class ControlMapControl(BaseModel):
    """One control activity, or evidence item, of the framework.

    An activity the ledger cannot speak to is listed with no evidence and a
    ``shows`` line that says so: the extract prints it, so a reader sees what
    is not covered as well as what is.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: str = Field(min_length=1)
    shows: str = Field(min_length=1)
    evidence: list[ControlMapEvidence] = Field(default_factory=list)


class ControlMapV1(BaseModel):
    """A control map file (``docs/control-maps/*.yaml``)."""

    model_config = ConfigDict(extra="forbid")

    control_map_schema_version: Literal["0.1"]
    framework: str = Field(min_length=1)
    framework_label: str = Field(min_length=1)
    source: str = Field(min_length=1)
    note: str = Field(min_length=1)
    controls: list[ControlMapControl] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> ControlMapV1:
        ids = [control.id for control in self.controls]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        if duplicates:
            raise ValueError(f"control ids appear more than once: {duplicates}")
        return self


class ControlEvidenceGapV1(BaseModel):
    """What one gap rule found for one cited row kind.

    ``not_checked`` is a value, never an omission: a rule the run could not
    apply (``observation_interval`` without ``--expected-interval-days``) is
    printed as not checked rather than left out.
    """

    model_config = ConfigDict(extra="forbid")

    rule: ControlGapRule
    status: ControlGapStatus
    count: int = Field(ge=0)
    row_ids: list[str] = Field(default_factory=list)
    stretches: list[RegistryDriftGapV1] = Field(default_factory=list)
    note: str | None = None


class ControlEvidenceItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    row_kind: RegistryRowKind
    row_count: int = Field(ge=0)
    counts: dict[str, int] = Field(default_factory=dict)
    row_ids: list[str] = Field(default_factory=list)
    gaps: list[ControlEvidenceGapV1] = Field(default_factory=list)


class ControlEvidenceControlV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    status: str
    shows: str
    evidence: list[ControlEvidenceItemV1] = Field(default_factory=list)
    gap_count: int = Field(ge=0)


class ControlEvidenceMapRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    control_map_schema_version: str
    framework: str
    framework_label: str
    source: str
    note: str


class ControlEvidenceChainHeadV1(BaseModel):
    """The last row of one repository's chain in the ledger the extract read."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    row_id: str


class ControlEvidenceExtractRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_count: int = Field(ge=0)


class ControlEvidenceV1(BaseModel):
    """``control-evidence.json``: rows, counts and gaps for one audit window."""

    model_config = ConfigDict(extra="forbid")

    control_evidence_schema_version: Literal["0.1"] = CONTROL_EVIDENCE_SCHEMA_VERSION
    statement: str
    registry: str
    map: ControlEvidenceMapRefV1
    since: str
    until: str
    repo: str | None = None
    expected_interval_days: int | None = None
    controls: list[ControlEvidenceControlV1]
    cited_row_count: int = Field(ge=0)
    anchor_row_ids: list[str] = Field(default_factory=list)
    chain_heads: list[ControlEvidenceChainHeadV1] = Field(default_factory=list)
    extract: ControlEvidenceExtractRefV1 | None = None
    recompute: str
    skipped_count: int = 0
    skipped_rows: list[RegistrySkippedRowV1] = Field(default_factory=list)


__all__ = [
    "CONTROL_EVIDENCE_FILENAME",
    "CONTROL_EVIDENCE_MARKDOWN_FILENAME",
    "CONTROL_EVIDENCE_SCHEMA_VERSION",
    "CONTROL_GAP_RULES",
    "CONTROL_MAP_COPY_FILENAME",
    "CONTROL_MAP_SCHEMA_VERSION",
    "GAP_RULE_ROW_KIND",
    "LEDGER_EXTRACT_FILENAME",
    "ControlEvidenceChainHeadV1",
    "ControlEvidenceControlV1",
    "ControlEvidenceExtractRefV1",
    "ControlEvidenceGapV1",
    "ControlEvidenceItemV1",
    "ControlEvidenceMapRefV1",
    "ControlEvidenceV1",
    "ControlGapRule",
    "ControlGapStatus",
    "ControlMapControl",
    "ControlMapEvidence",
    "ControlMapV1",
]
