"""``registry report --control-map``: an audit-window evidence extract (#1121).

Reads the registry ledger and a control map, and states, per control activity:
which rows of the window speak to it, their counts, and every gap the map's
rules find. Writes, beside that, the extract of exactly the rows it cited, so
an assessor can recompute every count from the extract alone without trusting
this program's summary.

Three rules carry it.

**Gaps are printed, not omitted.** A window with no row, an incomparable
comparison, an approval that was never supplied, a rule that could not be
applied: each is a stated line with its rows, never an absence a reader has to
notice.

**The extract recomputes the view.** It holds the cited rows, the rows of the
cited kinds that carry no readable time (counted as ``undated``), and, for a
repository the window holds no observation of, one observation outside it, so
the repository is still considered. Running the same command over the extract
gives the same ``controls`` block.

**No word asserts compliance.** The view states rows, counts and gaps. Whether
they satisfy a control is the assessor's decision, and
``release_decision.decision`` remains the only release gate.

Pure and deterministic: no clock, no network, no path outside what the caller
names.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import yaml
from pydantic import ValidationError

from agents_shipgate.core.drift_observation import drift_report, event_moment
from agents_shipgate.schemas.control_evidence import (
    CONTROL_EVIDENCE_SCHEMA_VERSION,
    CONTROL_MAP_COPY_FILENAME,
    ControlEvidenceChainHeadV1,
    ControlEvidenceControlV1,
    ControlEvidenceExtractRefV1,
    ControlEvidenceGapV1,
    ControlEvidenceItemV1,
    ControlEvidenceMapRefV1,
    ControlEvidenceV1,
    ControlMapEvidence,
    ControlMapV1,
)
from agents_shipgate.schemas.registry import RegistryRowV1, RegistrySkippedRowV1

STATEMENT = (
    "Rows, counts and gaps from the registry ledger for one window, arranged by "
    "the control map named here. Judging them against each control is the "
    "assessor's decision. release_decision.decision remains the only release gate."
)

_NOT_APPROVED = frozenset({"not_approved", "changes_requested", "approved_stale"})


class ControlMapError(ValueError):
    """The control map cannot be read, or the window cannot be applied."""


def load_control_map(path: Path) -> tuple[ControlMapV1, str]:
    """The map and the sha256 of its bytes."""

    control_map, raw = read_control_map(path)
    return control_map, hashlib.sha256(raw).hexdigest()


def read_control_map(path: Path) -> tuple[ControlMapV1, bytes]:
    """The map and the bytes it was read from, read once.

    ``registry report --control-map --out`` ships those bytes beside the
    extract, so the map an assessor recomputes with is the one that was hashed.
    """

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ControlMapError(f"could not read control map {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ControlMapError(f"control map {path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ControlMapError(f"control map {path} must be a YAML mapping")
    try:
        return ControlMapV1.model_validate(data), raw
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        raise ControlMapError(
            f"control map {path} is not a control_map_schema_version 0.1 file: "
            f"{where}: {first.get('msg')}"
        ) from exc


def control_evidence(
    *,
    control_map: ControlMapV1,
    map_path: str,
    map_sha256: str,
    registry: str,
    rows: Sequence[RegistryRowV1],
    lines: Sequence[str],
    skipped: Sequence[RegistrySkippedRowV1],
    since: date,
    until: date,
    repo: str | None = None,
    expected_interval_days: int | None = None,
    recompute: str,
    extract: ControlEvidenceExtractRefV1 | None = None,
) -> tuple[ControlEvidenceV1, list[str]]:
    """The evidence view, and the ledger lines of its extract in ledger order.

    ``lines`` holds each row's line as the ledger stores it, so the extract is
    those bytes and an assessor can find every one in the ledger it came from.
    """

    if since > until:
        raise ControlMapError(f"--since {since} is after --until {until}")
    if len(lines) != len(rows):  # pragma: no cover - the loader keeps them paired
        raise ControlMapError("rows and ledger lines are not paired")
    start = datetime.combine(since, time.min, UTC)
    end = datetime.combine(until + timedelta(days=1), time.min, UTC)
    indexed = [
        (index, row)
        for index, row in enumerate(rows)
        if repo is None or row.repo == repo
    ]

    cited: set[int] = set()
    anchors: set[int] = set()
    controls: list[ControlEvidenceControlV1] = []
    for control in control_map.controls:
        items = []
        for evidence in control.evidence:
            item, item_cited, item_anchors = _evidence_item(
                evidence,
                indexed=indexed,
                rows=rows,
                start=start,
                end=end,
                since=since,
                until=until,
                repo=repo,
                expected_interval_days=expected_interval_days,
                registry=registry,
                skipped=skipped,
            )
            items.append(item)
            cited |= item_cited
            anchors |= item_anchors
        controls.append(
            ControlEvidenceControlV1(
                id=control.id,
                title=control.title,
                status=control.status,
                shows=control.shows,
                evidence=items,
                gap_count=sum(
                    gap.count for item in items for gap in item.gaps if gap.status == "found"
                ),
            )
        )

    anchors -= cited
    heads: dict[str, str] = {}
    for _index, row in indexed:
        heads[row.repo] = row.row_id
    view = ControlEvidenceV1(
        control_evidence_schema_version=CONTROL_EVIDENCE_SCHEMA_VERSION,
        statement=STATEMENT,
        registry=registry,
        map=ControlEvidenceMapRefV1(
            path=map_path,
            sha256=map_sha256,
            control_map_schema_version=control_map.control_map_schema_version,
            framework=control_map.framework,
            framework_label=control_map.framework_label,
            source=control_map.source,
            note=control_map.note,
        ),
        since=since.isoformat(),
        until=until.isoformat(),
        repo=repo,
        expected_interval_days=expected_interval_days,
        controls=controls,
        cited_row_count=len(cited),
        anchor_row_ids=sorted(rows[index].row_id for index in anchors),
        chain_heads=[
            ControlEvidenceChainHeadV1(repo=name, row_id=row_id)
            for name, row_id in sorted(heads.items())
        ],
        extract=extract,
        recompute=recompute,
        skipped_count=len(skipped),
        skipped_rows=list(skipped),
    )
    return view, [lines[index] for index in sorted(cited | anchors)]


def extract_bytes(extract_lines: Sequence[str]) -> bytes:
    """The extract file, exactly: UTF-8, each line ended by ``\\n``.

    Written as these bytes and hashed as these bytes, so no platform's text
    mode (CRLF on Windows) can make the file disagree with its reference.
    """

    return "".join(f"{line}\n" for line in extract_lines).encode("utf-8")


def extract_ref(path_label: str, extract_lines: Sequence[str]) -> ControlEvidenceExtractRefV1:
    """The reference ``control-evidence.json`` keeps to the extract it ships with."""

    return ControlEvidenceExtractRefV1(
        path=path_label,
        sha256=hashlib.sha256(extract_bytes(extract_lines)).hexdigest(),
        row_count=len(extract_lines),
    )


def _evidence_item(
    evidence: ControlMapEvidence,
    *,
    indexed: Sequence[tuple[int, RegistryRowV1]],
    rows: Sequence[RegistryRowV1],
    start: datetime,
    end: datetime,
    since: date,
    until: date,
    repo: str | None,
    expected_interval_days: int | None,
    registry: str,
    skipped: Sequence[RegistrySkippedRowV1],
) -> tuple[ControlEvidenceItemV1, set[int], set[int]]:
    of_kind = [(index, row) for index, row in indexed if row.row_kind == evidence.row_kind]
    in_window: list[tuple[int, RegistryRowV1]] = []
    undated: list[tuple[int, RegistryRowV1]] = []
    for index, row in of_kind:
        moment = event_moment(row.event_time)
        if moment is None:
            undated.append((index, row))
        elif start <= moment < end:
            in_window.append((index, row))
    selected = [row for _, row in in_window]

    counts: dict[str, int] = {}
    if evidence.row_kind == "drift_observation":
        for status in ("clean", "drift", "incomparable"):
            counts[status] = sum(
                1 for row in selected if row.observation and row.observation.status == status
            )
    else:
        for row in selected:
            key = f"approval.{row.approval_status or 'not_recorded'}"
            counts[key] = counts.get(key, 0) + 1
    if undated:
        counts["undated"] = len(undated)

    gaps: list[ControlEvidenceGapV1] = []
    anchors: set[int] = set()
    for rule in evidence.gaps:
        if rule == "no_rows_in_window":
            gaps.append(
                ControlEvidenceGapV1(
                    rule=rule,
                    status="found" if not selected else "none",
                    count=0 if selected else 1,
                    note=(
                        None
                        if selected
                        else f"no {evidence.row_kind} row of the window"
                    ),
                )
            )
        elif rule == "observation_interval":
            if expected_interval_days is None:
                gaps.append(
                    ControlEvidenceGapV1(
                        rule=rule,
                        status="not_checked",
                        count=0,
                        note="pass --expected-interval-days to check the interval",
                    )
                )
                continue
            report = drift_report(
                registry=registry,
                rows=[row for _, row in indexed],
                skipped=skipped,
                repo=repo,
                since=since,
                until=until,
                expected_interval_days=expected_interval_days,
            )
            gaps.append(
                ControlEvidenceGapV1(
                    rule=rule,
                    status="found" if report.gaps else "none",
                    count=len(report.gaps),
                    stretches=report.gaps,
                )
            )
            anchors |= _interval_anchors(of_kind, in_window)
        else:
            matched = sorted(row.row_id for row in selected if _matches(rule, row))
            gaps.append(
                ControlEvidenceGapV1(
                    rule=rule,
                    status="found" if matched else "none",
                    count=len(matched),
                    row_ids=matched,
                )
            )

    item = ControlEvidenceItemV1(
        label=evidence.label,
        row_kind=evidence.row_kind,
        row_count=len(selected),
        counts=counts,
        row_ids=sorted(row.row_id for row in selected),
        gaps=gaps,
    )
    cited = {index for index, _ in in_window} | {index for index, _ in undated}
    return item, cited, anchors


def _interval_anchors(
    of_kind: Sequence[tuple[int, RegistryRowV1]],
    in_window: Sequence[tuple[int, RegistryRowV1]],
) -> set[int]:
    """One observation per repository the window holds none of.

    The interval rule considers every repository the ledger has observed, so a
    repository that went silent for the whole window is one gap across it. The
    extract keeps that repository's last observation so a recompute over the
    extract considers it too.
    """

    observed_in_window = {row.repo for _, row in in_window}
    last: dict[str, int] = {}
    for index, row in of_kind:
        if row.repo not in observed_in_window:
            last[row.repo] = index
    return set(last.values())


def _matches(rule: str, row: RegistryRowV1) -> bool:
    observation = row.observation
    if rule == "observation_incomparable":
        # The host comparison itself, not the row's status: a run whose
        # capability diff found drift has status ``drift`` even when its host
        # comparison could not be made, and that comparison is still missing.
        return observation is not None and observation.host.comparison_status != "comparable"
    if rule == "inventory_issues":
        return observation is not None and observation.host.issue_count > 0
    if rule == "capability_comparison_absent":
        return observation is not None and observation.capability is None
    if rule == "definition_digests_absent":
        return not row.capability_lock.get("definition_set_hash")
    if rule == "approval_not_supplied":
        return row.approval_status in (None, "not_supplied")
    if rule == "approval_not_approved":
        return row.approval_status in _NOT_APPROVED
    raise ControlMapError(f"unknown gap rule {rule!r}")  # pragma: no cover


def render_control_evidence_markdown(view: ControlEvidenceV1) -> str:
    """``control-evidence.md``: the same view, for a reader."""

    lines = [
        f"# Control evidence: {view.map.framework_label}",
        "",
        f"Window: {view.since} to {view.until} (UTC days, inclusive). "
        f"Ledger: `{view.registry}`"
        + (f", repository `{view.repo}`" if view.repo else "")
        + f". Map: `{view.map.path}` (sha256 `{view.map.sha256[:12]}`).",
        "",
        view.statement,
        "",
        f"Mapping source: {view.map.source}. {view.map.note}",
        "",
        "| Control | Status | Evidence | Rows | Gaps |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for control in view.controls:
        if not control.evidence:
            lines.append(
                f"| {control.id} {control.title} | {control.status} | "
                "not evidenced by this ledger | 0 | 0 |"
            )
            continue
        for item in control.evidence:
            found = sum(gap.count for gap in item.gaps if gap.status == "found")
            lines.append(
                f"| {control.id} {control.title} | {control.status} | {item.label} | "
                f"{item.row_count} | {found} |"
            )
    for control in view.controls:
        lines.extend(["", f"## {control.id} · {control.title} ({control.status})", "", control.shows])
        for item in control.evidence:
            counts = ", ".join(f"{name} {value}" for name, value in item.counts.items())
            lines.extend(
                [
                    "",
                    f"**{item.label}** ({item.row_kind}): {item.row_count} row(s)"
                    + (f"; {counts}" if counts else "")
                    + ".",
                ]
            )
            for gap in item.gaps:
                lines.append(f"- `{gap.rule}`: {_gap_text(gap)}")
    lines.extend(["", "## Recompute", ""])
    if view.extract is not None:
        lines.append(
            f"`{view.extract.path}` holds {view.extract.row_count} ledger line(s), byte "
            f"for byte (sha256 `{view.extract.sha256[:12]}`): the "
            f"{view.cited_row_count} this view cites and "
            f"{len(view.anchor_row_ids)} observation(s) outside the window that keep "
            "a repository with no run in it in the interval check. "
            f"`{CONTROL_MAP_COPY_FILENAME}` is the map, byte for byte (sha256 "
            f"`{view.map.sha256[:12]}`). Run, from this directory:"
        )
    else:
        lines.append("Run:")
    lines.extend(
        [
            "",
            "```bash",
            view.recompute,
            "```",
            "",
            "Its `controls` block equals this view's. Verify the full ledger's hash "
            f"chain with `agents-shipgate registry verify --registry {view.registry}`; "
            "at the time of this view the chains ended at:",
            "",
        ]
    )
    for head in view.chain_heads:
        lines.append(f"- `{head.repo or '(no repository label)'}`: `{head.row_id}`")
    if view.skipped_count:
        lines.extend(["", f"{view.skipped_count} malformed ledger line(s) were skipped."])
    return "\n".join(lines) + "\n"


def _gap_text(gap: ControlEvidenceGapV1) -> str:
    if gap.status == "not_checked":
        return f"not checked ({gap.note})"
    if gap.status == "none":
        return "none"
    if gap.stretches:
        spans = "; ".join(
            f"{stretch.repo or '(no label)'} {stretch.start} to {stretch.end}"
            for stretch in gap.stretches
        )
        return f"{gap.count} stretch(es) with no observation: {spans}"
    if gap.row_ids:
        shown = ", ".join(f"`{row_id}`" for row_id in gap.row_ids[:20])
        more = f" and {len(gap.row_ids) - 20} more" if len(gap.row_ids) > 20 else ""
        return f"{gap.count} row(s): {shown}{more}"
    return gap.note or f"{gap.count}"


__all__ = [
    "STATEMENT",
    "ControlMapError",
    "control_evidence",
    "extract_bytes",
    "extract_ref",
    "load_control_map",
    "read_control_map",
    "render_control_evidence_markdown",
]
