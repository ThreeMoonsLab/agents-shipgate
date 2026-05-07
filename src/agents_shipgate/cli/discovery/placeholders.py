"""Detect ``CHANGE_ME`` placeholders in a rendered manifest.

Pulled out of ``cli/main.py`` so both ``init`` (which has always reported
placeholders in its JSON output) and the new ``doctor`` diagnostic
(``SHIP-DIAG-CHANGE-ME-PLACEHOLDERS``) share one implementation.

The ``init`` callers historically saw ``[{path, current}]``; the doctor
diagnostic also wants the line number to point an ``edit`` action at
``shipgate.yaml:<line>``. Both are returned in a single richer payload.
"""

from __future__ import annotations


def collect_placeholders(template: str) -> list[dict[str, object]]:
    """Find ``CHANGE_ME`` markers in ``template`` and return their
    YAML-pointer-ish location, the original value, and the 1-indexed
    line number on which the placeholder appears."""
    placeholders: list[dict[str, object]] = []
    section_path: list[str] = []
    last_indent = -1
    for index, line in enumerate(template.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while section_path and last_indent >= indent:
            section_path.pop()
            last_indent -= 2
        stripped = line.strip()
        if stripped.endswith(":") and "CHANGE_ME" not in stripped:
            section_path.append(stripped[:-1])
            last_indent = indent
            continue
        if "CHANGE_ME" in line:
            key = stripped.split(":", 1)[0].lstrip("- ").strip()
            placeholders.append(
                {
                    "path": ".".join(
                        [*section_path, key] if key else section_path
                    )
                    or "<root>",
                    "current": "CHANGE_ME",
                    "line": index,
                }
            )
    return placeholders
