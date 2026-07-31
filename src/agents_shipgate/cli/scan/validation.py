from __future__ import annotations

from pathlib import Path

from agents_shipgate.cli.discovery.placeholders import collect_placeholders
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import load_text_file


def _resolve_source_paths(
    manifest, base_dir: Path, config_path: Path
) -> list[dict[str, object]]:
    """Return required tool_sources whose declared path is unusable.

    Two failure modes are flagged so doctor can surface them as a
    ``SHIP-DIAG-MISSING-SOURCE-FILE`` diagnostic instead of crashing in
    a downstream loader:

    - ``reason="missing"`` — the file does not exist.
    - ``reason="outside_manifest_dir"`` — the file exists but escapes the
      manifest's containment boundary (loaders mirror this check and
      would raise ``InputParseError``).

    Optional sources are not reported here — the existing
    ``_load_sources`` flow handles them with a warning. Returned entries
    carry the source id, the declared path string, the 1-indexed line
    number in the manifest text where the path appears (best-effort),
    and the failure reason.
    """
    unresolved: list[dict[str, object]] = []
    try:
        manifest_text = load_text_file(config_path)
    except InputParseError:
        manifest_text = ""
    text_lines = manifest_text.splitlines()
    base_resolved = base_dir.resolve()
    for source in manifest.tool_sources:
        if source.optional:
            continue
        if source.path is None:
            continue
        raw_path = Path(source.path)
        candidate = (
            raw_path if raw_path.is_absolute() else base_resolved / raw_path
        ).resolve()
        if not candidate.exists():
            reason = "missing"
        else:
            try:
                candidate.relative_to(base_resolved)
            except ValueError:
                reason = "outside_manifest_dir"
            else:
                continue
        line_no: int | None = None
        needle = f"path: {source.path}"
        for index, line in enumerate(text_lines, start=1):
            if needle in line:
                line_no = index
                break
        unresolved.append(
            {
                "id": source.id,
                "declared_path": source.path,
                "line": line_no,
                "reason": reason,
            }
        )
    return unresolved


def _manifest_placeholder_warnings(config_path: Path) -> list[str]:
    """Return source-warning strings for each ``CHANGE_ME`` placeholder
    surviving in the manifest text.

    Doctor already surfaces these as ``SHIP-DIAG-CHANGE-ME-PLACEHOLDERS``
    diagnostics; the same fact also needs to flow into the scan so the
    existing ``source_warning_count > 0 → review_required`` branch in
    release_decision.evidence_coverage trips. Read failures (missing
    file, non-UTF8 content) yield no warnings — the manifest loader runs
    immediately before and will have already raised a structured error
    in that case.
    """
    try:
        manifest_text = load_text_file(config_path)
    except InputParseError:
        return []
    placeholders = collect_placeholders(manifest_text)
    name = config_path.name
    return [
        f"{name}:{entry['line']} — CHANGE_ME placeholder at "
        f"{entry.get('path', '<root>')!r}; replace before treating this "
        "report as evidence."
        for entry in placeholders
    ]
