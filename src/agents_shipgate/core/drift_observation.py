"""Scheduled post-approval comparisons, recorded as registry rows (#1119).

``audit --host --drift`` compares the repository's coding-agent host grants
with the acknowledged baseline, and ``capability diff`` compares a fresh
capability export with the reviewed lock. Each run's result used to live only
in its workflow log. This module projects one run into a
:class:`RegistryDriftObservationV1` for the ledger, and reads a window of those
rows back as :class:`RegistryDriftReportV1`.

The projection keeps identities, kinds and counts. It copies no grant value:
no command, argument, URL, env value or rule text, which stay in the audit
payload. Pure and deterministic; no clock, no network. An observation is
evidence that monitoring ran and what it found. It never decides whether
anything may merge or ship: ``release_decision.decision`` is the only gate.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from agents_shipgate.schemas.registry import (
    RegistryCapabilityComparisonV1,
    RegistryCapabilityLockIdentityV1,
    RegistryDriftGapV1,
    RegistryDriftObservationV1,
    RegistryDriftReportV1,
    RegistryDriftRepoSummaryV1,
    RegistryHostArtifactChangeV1,
    RegistryHostComparisonV1,
    RegistryHostCoverageChangeV1,
    RegistryHostGrantChangeV1,
    RegistryRowV1,
    RegistrySkippedRowV1,
    drift_observation_status,
)

#: A kind, host or reason code is copied only when it has this shape. Every
#: value the audit emits for these fields does; anything else (a path, a
#: message) is not a code and is not copied.
_CODE = re.compile(r"^[a-z][a-z0-9_.\-]{0,63}$")
_REASON = re.compile(r"^[A-Za-z0-9_.:>\-]{1,120}$")
_SIGNAL_KIND = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: The lock diff lists whose ids an observation records. ``evidence_changed``
#: (the same fact read from moved evidence) is counted in ``summary`` only:
#: it is not a change to what the agent may do.
_CAPABILITY_ID_LISTS = ("added", "removed", "reidentified", "changed", "definition_changed")
_CAPABILITY_SUMMARY_KEYS = (
    "added",
    "removed",
    "reidentified",
    "changed",
    "evidence_changed",
    "unchanged",
    "definition_changed",
)


class ObservationError(ValueError):
    """The input is not a comparison an observation can be made from."""


class CapabilityDiffError(ObservationError):
    """The capability diff, not the host comparison, is what cannot be read."""


def drift_observation(
    host_drift: Any,
    *,
    source_sha256: str,
    capability_diff: Any = None,
    capability_diff_sha256: str | None = None,
    trigger: str | None = None,
) -> RegistryDriftObservationV1:
    """Project one ``audit --host --drift --json`` payload (and optionally the
    same run's ``capability diff`` JSON) into an observation.

    Raises :class:`ObservationError` when the payload is not a drift
    comparison: a host inventory, a baseline or another file is refused rather
    than recorded as a run that found nothing.
    """

    host = _host_comparison(host_drift)
    capability = None
    if capability_diff is not None:
        if capability_diff_sha256 is None:
            raise CapabilityDiffError("a capability diff needs its file hash")
        capability = _capability_comparison(capability_diff, source_sha256=capability_diff_sha256)
    return RegistryDriftObservationV1(
        status=drift_observation_status(host, capability),
        source_sha256=source_sha256,
        trigger=_code(trigger),
        host=host,
        capability=capability,
    )


def parse_event_time(value: str) -> str:
    """A declared observation time, normalized to UTC seconds (``…Z``).

    Requires an ISO 8601 time with an offset: a time without one cannot be
    placed in a window, and the engine has no clock to assume one from.
    """

    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ObservationError(
            f"{value!r} is not an ISO 8601 time; pass one such as 2026-11-06T06:17:00Z"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ObservationError(
            f"{value!r} has no UTC offset; pass one such as 2026-11-06T06:17:00Z"
        )
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def drift_report(
    *,
    registry: str,
    rows: Sequence[RegistryRowV1],
    skipped: Sequence[RegistrySkippedRowV1],
    repo: str | None = None,
    since: date | None = None,
    until: date | None = None,
    expected_interval_days: int | None = None,
) -> RegistryDriftReportV1:
    """The observations of ``rows`` in ``[since, until]`` (UTC days, inclusive).

    With ``expected_interval_days``, a stretch between two observations of one
    repository, or between the window's edge and its first or last
    observation, that is longer than that many days is a gap. A repository is
    considered when the ledger holds any observation of it, or when it is the
    ``repo`` asked for. An observation whose ``event_time`` cannot be read is
    counted in ``undated_count`` and placed in no window.
    """

    if since is not None and until is not None and since > until:
        raise ObservationError(f"--since {since} is after --until {until}")
    window_start = datetime.combine(since, time.min, UTC) if since is not None else None
    window_end = (
        datetime.combine(until + timedelta(days=1), time.min, UTC) if until is not None else None
    )
    windowed = window_start is not None or window_end is not None

    observations = [
        row
        for row in rows
        if row.row_kind == "drift_observation"
        and row.observation is not None
        and (repo is None or row.repo == repo)
    ]
    considered = sorted({row.repo for row in observations} | ({repo} if repo is not None else set()))

    dated: list[tuple[datetime, RegistryRowV1]] = []
    undated: list[RegistryRowV1] = []
    for row in observations:
        moment = _event_moment(row.event_time)
        if moment is None:
            undated.append(row)
        else:
            dated.append((moment, row))
    in_window = [
        (moment, row)
        for moment, row in dated
        if (window_start is None or moment >= window_start)
        and (window_end is None or moment < window_end)
    ]
    in_window.sort(key=lambda item: (item[1].repo, item[0], item[1].row_id))
    selected = [row for _, row in in_window]
    if not windowed:
        selected.extend(sorted(undated, key=lambda row: (row.repo, row.row_id)))

    gaps: list[RegistryDriftGapV1] = []
    if expected_interval_days is not None:
        limit = timedelta(days=expected_interval_days)
        for name in considered:
            points: list[tuple[datetime, str | None]] = []
            if window_start is not None:
                points.append((window_start, None))
            points.extend((moment, row.row_id) for moment, row in in_window if row.repo == name)
            if window_end is not None:
                points.append((window_end, None))
            for (start, after_id), (end, before_id) in zip(points, points[1:], strict=False):
                if end - start > limit:
                    gaps.append(
                        RegistryDriftGapV1(
                            repo=name,
                            start=_stamp(start),
                            end=_stamp(end),
                            duration_seconds=int((end - start).total_seconds()),
                            after_row_id=after_id,
                            before_row_id=before_id,
                        )
                    )

    repos = []
    for name in considered:
        mine = [(moment, row) for moment, row in in_window if row.repo == name]
        repos.append(
            RegistryDriftRepoSummaryV1(
                repo=name,
                observation_count=len(mine),
                clean_count=_count_status(mine, "clean"),
                drift_count=_count_status(mine, "drift"),
                incomparable_count=_count_status(mine, "incomparable"),
                first_observed_at=_stamp(mine[0][0]) if mine else None,
                last_observed_at=_stamp(mine[-1][0]) if mine else None,
                gap_count=sum(1 for gap in gaps if gap.repo == name),
            )
        )

    statuses = [row.observation.status for row in selected if row.observation is not None]
    return RegistryDriftReportV1(
        registry=registry,
        repo=repo,
        since=since.isoformat() if since is not None else None,
        until=until.isoformat() if until is not None else None,
        expected_interval_days=expected_interval_days,
        observation_count=len(selected),
        clean_count=statuses.count("clean"),
        drift_count=statuses.count("drift"),
        incomparable_count=statuses.count("incomparable"),
        host_drift_count=sum(
            1
            for row in selected
            if row.observation is not None and row.observation.host.has_drift is True
        ),
        capability_drift_count=sum(
            1
            for row in selected
            if row.observation is not None
            and row.observation.capability is not None
            and row.observation.capability.has_drift
        ),
        undated_count=len(undated),
        gap_count=len(gaps),
        gaps=gaps,
        repos=repos,
        skipped_count=len(skipped),
        skipped_rows=list(skipped),
        rows=selected,
    )


def drift_report_needs_review(report: RegistryDriftReportV1) -> bool:
    """What ``--fail-on-drift`` exits ``20`` on: a run that found drift, a run
    that could not compare, or a gap in the monitoring."""

    return bool(report.drift_count or report.incomparable_count or report.gap_count)


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------


def _host_comparison(payload: Any) -> RegistryHostComparisonV1:
    if not isinstance(payload, dict):
        raise ObservationError("the observation must be the JSON object `audit --host --drift` prints")
    if "comparison_status" not in payload:
        if "host_grants_inventory_schema_version" in payload or "inventory" in payload:
            raise ObservationError(
                "this is a host inventory or baseline, not a drift comparison: "
                "pass the output of `shipgate audit --host --drift --json`"
            )
        raise ObservationError(
            "missing comparison_status: pass the output of `shipgate audit --host --drift --json`"
        )
    version = payload.get("host_grants_schema_version")
    if not isinstance(version, str) or not version:
        raise ObservationError("missing host_grants_schema_version")
    status = payload.get("comparison_status")
    if status not in {"comparable", "incomparable"}:
        raise ObservationError(f"unknown comparison_status {status!r}")
    baseline_file = payload.get("baseline_file")
    if not isinstance(baseline_file, str) or not baseline_file:
        raise ObservationError("missing baseline_file")
    has_drift = payload.get("has_drift")
    if has_drift is not None and not isinstance(has_drift, bool):
        raise ObservationError("has_drift must be true, false or null")
    try:
        return RegistryHostComparisonV1(
            host_grants_schema_version=version,
            scope=_code(payload.get("scope")),
            baseline_file=_display_baseline(baseline_file),
            comparison_status=status,
            baseline_sha256=_sha256_or_none(payload.get("baseline_sha256")),
            current_sha256=_sha256_or_none(payload.get("current_sha256")),
            has_drift=has_drift,
            incomparable_reasons=sorted(
                {
                    reason if _REASON.match(reason) else "unreadable_reason"
                    for reason in _strings(payload.get("incomparable_reasons"))
                }
            ),
            grant_changes=sorted(
                (
                    RegistryHostGrantChangeV1(grant_id=item_id, **_side_codes(item), change=change)
                    for item_id, item, change in _changes(payload.get("changes"), "grant_id")
                ),
                key=lambda item: (item.grant_id, item.change),
            ),
            artifact_changes=sorted(
                (
                    RegistryHostArtifactChangeV1(
                        artifact_id=item_id, **_side_codes(item), change=change
                    )
                    for item_id, item, change in _changes(
                        payload.get("artifact_changes"), "artifact_id"
                    )
                ),
                key=lambda item: (item.artifact_id, item.change),
            ),
            coverage_changes=sorted(
                (
                    RegistryHostCoverageChangeV1(host=item_id, change=change)
                    for item_id, _, change in _changes(payload.get("coverage_changes"), "host")
                ),
                key=lambda item: (item.host, item.change),
            ),
            expansion_signal_kinds=_signal_kinds(payload.get("expansion_signals")),
            issue_count=len(_list(payload.get("issues"), "issues")),
        )
    except ValueError as exc:
        if isinstance(exc, ObservationError):
            raise
        raise ObservationError(f"the drift payload does not hold together: {_first_error(exc)}") from exc


def _changes(value: Any, id_key: str) -> list[tuple[str, dict[str, Any], str]]:
    out = []
    for index, item in enumerate(_list(value, id_key)):
        if not isinstance(item, dict):
            raise ObservationError(f"change {index} is not an object")
        item_id = item.get(id_key)
        if not isinstance(item_id, str) or not item_id:
            raise ObservationError(f"change {index} has no {id_key}")
        before, after = item.get("baseline"), item.get("current")
        if before is None and after is None:
            raise ObservationError(f"change {index} ({item_id}) has neither side")
        change = "added" if before is None else "removed" if after is None else "changed"
        out.append((item_id, after if isinstance(after, dict) else before or {}, change))
    return out


def _side_codes(side: dict[str, Any]) -> dict[str, str | None]:
    return {"kind": _code(side.get("kind")), "host": _code(side.get("host"))}


def _signal_kinds(value: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for signal in _strings(value):
        kind = signal.split(":", 1)[0].strip()
        key = kind if _SIGNAL_KIND.match(kind) else "other"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _capability_comparison(payload: Any, *, source_sha256: str) -> RegistryCapabilityComparisonV1:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("capability_lock_diff_schema_version"), str
    ):
        raise CapabilityDiffError(
            "the capability diff must be the JSON `agents-shipgate capability diff` writes"
        )
    summary_in = payload.get("summary")
    if not isinstance(summary_in, dict):
        raise CapabilityDiffError("the capability diff has no summary")
    summary: dict[str, int] = {}
    for key in _CAPABILITY_SUMMARY_KEYS:
        value = summary_in.get(key, 0)
        if type(value) is not int or value < 0:
            raise CapabilityDiffError(f"capability diff summary.{key} is not a count")
        summary[key] = value
    ids: dict[str, list[str]] = {}
    for key in _CAPABILITY_ID_LISTS:
        try:
            entries = _list(payload.get(key), f"capability diff {key}")
        except ObservationError as exc:
            raise CapabilityDiffError(str(exc)) from exc
        found = []
        for index, entry in enumerate(entries):
            entry_id = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(entry_id, str) or not entry_id:
                raise CapabilityDiffError(f"capability diff {key}[{index}] has no id")
            found.append(entry_id)
        if len(found) != summary[key]:
            raise CapabilityDiffError(
                f"capability diff summary.{key} is {summary[key]} but {len(found)} rows are listed"
            )
        ids[key] = sorted(found)
    return RegistryCapabilityComparisonV1(
        capability_lock_diff_schema_version=payload["capability_lock_diff_schema_version"],
        source_sha256=source_sha256,
        base=_lock_identity(payload.get("base")),
        head=_lock_identity(payload.get("head")),
        summary=summary,
        added_ids=ids["added"],
        removed_ids=ids["removed"],
        reidentified_ids=ids["reidentified"],
        changed_ids=ids["changed"],
        definition_changed_ids=ids["definition_changed"],
        has_drift=any(ids[key] for key in _CAPABILITY_ID_LISTS),
    )


def _lock_identity(value: Any) -> RegistryCapabilityLockIdentityV1:
    ref = value if isinstance(value, dict) else {}
    count = ref.get("capability_count")
    return RegistryCapabilityLockIdentityV1(
        capability_lock_schema_version=_str_or_none(ref.get("capability_lock_schema_version")),
        semantic_capability_set_hash=_str_or_none(ref.get("semantic_capability_set_hash")),
        definition_set_hash=_str_or_none(ref.get("definition_set_hash")),
        capability_count=count if type(count) is int else None,
    )


def _display_baseline(path: str) -> str:
    """The baseline path as the audit was given it, reduced to its name when
    it is absolute: a path outside the repository can name a home directory."""

    if path.startswith("~") or PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute():
        return PureWindowsPath(path).name if "\\" in path else PurePosixPath(path).name
    return path


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ObservationError(f"{label} must be a list")
    return value


def _strings(value: Any) -> list[str]:
    return [item for item in _list(value, "list") if isinstance(item, str)]


def _code(value: Any) -> str | None:
    return value if isinstance(value, str) and _CODE.match(value) else None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _sha256_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def _first_error(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        listed = errors()
        if listed:
            return str(listed[0].get("msg"))
    return str(exc)


def event_moment(value: str | None) -> datetime | None:
    """A row's ``event_time`` as a UTC instant, or ``None`` when it cannot be read."""

    return _event_moment(value)


def _event_moment(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count_status(rows: Sequence[tuple[datetime, RegistryRowV1]], status: str) -> int:
    return sum(1 for _, row in rows if row.observation is not None and row.observation.status == status)


__all__ = [
    "CapabilityDiffError",
    "ObservationError",
    "drift_observation",
    "drift_report",
    "drift_report_needs_review",
    "event_moment",
    "parse_event_time",
]
