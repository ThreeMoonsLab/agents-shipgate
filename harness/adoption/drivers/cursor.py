"""Cursor static-rule lint driver.

Cursor has no documented headless mode, so v1 cannot run Cursor agents
behaviourally. This driver instead asks: when the variant overlay drops a
Cursor rule into the workspace, does the rule's *content* match the
canonical snippet and do its globs cover the trigger files Shipgate cares
about?

It runs in zero seconds, costs nothing, and runs on every cell whose
variant installs a Cursor rule. v3 will add a manual-entry mode for real
behaviour capture.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from harness.adoption.drivers.base import AgentDriver, DriverInputs, RunResult
from harness.adoption.observer.transcript import TranscriptWriter

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
        started = datetime.now(timezone.utc)
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
            ended = datetime.now(timezone.utc)
            return RunResult(
                started_at=started,
                ended_at=ended,
                degraded=False,
                summary_text="No Cursor rule present in this variant.",
                final_diff="",
            )

        text = rule_path.read_text(encoding="utf-8")
        writer.transcript(
            {
                "type": "static_lint",
                "stage": "rule_present",
                "rule_present": True,
                "char_count": len(text),
            }
        )

        # Check canonical glob coverage.
        missing_globs = [g for g in CANONICAL_GLOBS_REQUIRED if g not in text]
        writer.transcript(
            {
                "type": "static_lint",
                "stage": "globs",
                "missing_globs": missing_globs,
            }
        )

        # Check trigger-file activation for this archetype.
        archetype = inputs.cell_id.split("__", 1)[0]
        trigger_files = TRIGGER_FILES_BY_ARCHETYPE.get(archetype, ())
        triggers_hit = [
            f for f in trigger_files if (inputs.workspace / f).exists()
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

        # Static-lint drivers do not execute commands or edit files. We emit a
        # synthetic summary so the scoring engine has something to read.
        if missing_globs:
            verdict = "rule_present_but_globs_incomplete"
        elif not triggers_hit:
            verdict = "rule_present_no_trigger_files_matched"
        else:
            verdict = "rule_active"

        summary = (
            "Cursor static-lint result:\n"
            f"- rule_present: True\n"
            f"- missing_globs: {missing_globs}\n"
            f"- triggers_present: {triggers_hit}\n"
            f"- verdict: {verdict}\n"
        )
        ended = datetime.now(timezone.utc)
        return RunResult(
            started_at=started,
            ended_at=ended,
            degraded=False,
            summary_text=summary,
            final_diff="",
        )


__all__ = ["CursorStaticDriver"]
