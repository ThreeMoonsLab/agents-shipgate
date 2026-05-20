"""Cursor static-rule lint driver.

Cursor has no documented headless mode, so v1 cannot run Cursor agents
behaviourally. This driver instead asks: when the variant overlay drops a
Cursor rule into the workspace, does its YAML frontmatter declare the
canonical globs, and do those declared globs actually match the
archetype's trigger files?

It runs in zero seconds, costs nothing, and runs on every cell whose
variant installs a Cursor rule. v3 will add a manual-entry mode for real
behaviour capture.

The implementation parses the ``.mdc`` YAML frontmatter rather than
substring-matching the file body — so globs mentioned only in prose or
comments do NOT count. Glob coverage against trigger files uses
:mod:`fnmatch` so the lint approximates how Cursor itself activates.
"""
from __future__ import annotations

import fnmatch
import re
from datetime import UTC, datetime

import yaml

from harness.adoption.drivers.base import DriverInputs, RunResult
from harness.adoption.observer.transcript import TranscriptWriter

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

CANONICAL_GLOBS_REQUIRED: tuple[str, ...] = (
    "shipgate.yaml",
    "**/*openapi*.yaml",
    "**/*mcp*.json",
    "**/*tools*.json",
    "n8n/*.json",
    "workflows/*.json",
    ".github/workflows/agents-shipgate.yml",
)
"""Globs the canonical Cursor rule must cover. Misses surface as warnings."""

TRIGGER_FILES_BY_ARCHETYPE: dict[str, tuple[str, ...]] = {
    "openai-agents-sdk": ("shipgate.yaml", "specs/support-tools.openapi.yaml"),
    "mcp-only": ("shipgate.yaml", "mcp/tools.json"),
    "openapi-only": ("shipgate.yaml", "specs/support.openapi.yaml"),
    "n8n": ("workflows/support-refund.json",),
    "langgraph": ("shipgate.yaml",),
    "adk-dynamic-toolset": ("shipgate.yaml", "specs/support.openapi.yaml"),
    "crewai": ("shipgate.yaml",),
    "clean-read-only": ("shipgate.yaml",),
}
"""For each archetype, the trigger files that exist in the workspace.

The driver checks that at least one rule glob matches at least one of these
trigger files. A failing match is captured as a warning, not a blocker —
the activation criterion fires once any glob covers the workspace.
"""


class CursorStaticDriver:
    name = "cursor-static"

    def run(self, inputs: DriverInputs, writer: TranscriptWriter) -> RunResult:
        started = datetime.now(UTC)
        rule_path = inputs.workspace / ".cursor" / "rules" / "agents-shipgate.mdc"

        if not rule_path.is_file():
            writer.transcript(
                {
                    "type": "static_lint",
                    "stage": "rule_present",
                    "rule_present": False,
                    "summary": "No .cursor/rules/agents-shipgate.mdc present.",
                }
            )
            ended = datetime.now(UTC)
            return RunResult(
                started_at=started,
                ended_at=ended,
                degraded=False,
                summary_text="No Cursor rule present in this variant.",
                final_diff="",
            )

        text = rule_path.read_text(encoding="utf-8")
        declared_globs, frontmatter_ok, parse_error = _parse_declared_globs(text)
        writer.transcript(
            {
                "type": "static_lint",
                "stage": "rule_present",
                "rule_present": True,
                "char_count": len(text),
                "frontmatter_ok": frontmatter_ok,
                "frontmatter_error": parse_error,
            }
        )

        # Glob check: every canonical glob must appear in the DECLARED list,
        # not anywhere in the body. Strings mentioned only in prose or
        # comments do not activate Cursor.
        missing_globs = [g for g in CANONICAL_GLOBS_REQUIRED if g not in declared_globs]
        writer.transcript(
            {
                "type": "static_lint",
                "stage": "globs",
                "declared_globs": declared_globs,
                "missing_globs": missing_globs,
            }
        )

        # Trigger-file activation: each archetype trigger file MUST match at
        # least one declared glob via fnmatch (Cursor's matching model).
        archetype = inputs.cell_id.split("__", 1)[0]
        trigger_files = TRIGGER_FILES_BY_ARCHETYPE.get(archetype, ())
        triggers_hit = [
            f
            for f in trigger_files
            if (inputs.workspace / f).exists()
            and any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(f, "*/" + g) for g in declared_globs)
        ]
        writer.transcript(
            {
                "type": "static_lint",
                "stage": "trigger_files",
                "archetype": archetype,
                "triggers_present": triggers_hit,
                "triggers_expected": list(trigger_files),
            }
        )

        if not frontmatter_ok:
            verdict = "rule_present_but_frontmatter_invalid"
        elif missing_globs:
            verdict = "rule_present_but_globs_incomplete"
        elif not triggers_hit:
            verdict = "rule_present_no_trigger_files_matched"
        else:
            verdict = "rule_active"

        summary = (
            "Cursor static-lint result:\n"
            f"- rule_present: True\n"
            f"- frontmatter_ok: {frontmatter_ok}\n"
            f"- declared_globs: {declared_globs}\n"
            f"- missing_globs: {missing_globs}\n"
            f"- triggers_present: {triggers_hit}\n"
            f"- verdict: {verdict}\n"
        )
        ended = datetime.now(UTC)
        return RunResult(
            started_at=started,
            ended_at=ended,
            degraded=False,
            summary_text=summary,
            final_diff="",
        )


def _parse_declared_globs(text: str) -> tuple[list[str], bool, str | None]:
    """Extract the ``globs:`` list from a Cursor ``.mdc`` rule.

    Returns ``(globs, frontmatter_ok, error_message)``. ``frontmatter_ok`` is
    True only when the file has a valid ``---`` YAML frontmatter block and
    ``globs:`` is present as a list of strings. A malformed rule (including
    one where globs appear only in body prose) yields ``[]``, ``False``, and
    a human-readable error.
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return [], False, "missing or malformed --- frontmatter block"
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        return [], False, f"frontmatter YAML parse error: {exc}"
    if not isinstance(meta, dict):
        return [], False, "frontmatter is not a YAML mapping"
    raw = meta.get("globs")
    if raw is None:
        return [], True, "frontmatter has no `globs:` field"
    if not isinstance(raw, list):
        return [], True, "`globs:` is not a list"
    declared = [g for g in raw if isinstance(g, str)]
    return declared, True, None


__all__ = ["CursorStaticDriver", "_parse_declared_globs"]
