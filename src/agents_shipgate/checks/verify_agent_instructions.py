"""SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED (§5.1 Tier B / Principle 3/4).

Agent-instruction files (AGENTS.md, CLAUDE.md, .cursor/rules/**, skills,
etc.) tell coding agents how to behave around the gate. A PR that edits
them could be removing a "do not weaken the gate" instruction — but
Shipgate is static and makes no LLM/NLP judgement, so it CANNOT prove
semantic weakening from text alone.

Per Principle 3 ("prompts are not controls — back every instruction with a
deterministic check") and §5.3 ("fail safe to review_required when the
semantic direction cannot be proven"), this check routes any verify-mode
modification of an agent-instruction trust root to **human review**: the
human must confirm the instructions were not weakened. It is deterministic
(fires on changed-file membership), never blocks on its own (medium), and
names the human-authority concern that the broader TRUST-ROOT-TOUCHED
finding does not.
"""

from __future__ import annotations

from agents_shipgate.checks._verify_common import (
    changed_files,
    touched,
    verification_active,
    verify_finding,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED"

_AGENT_INSTRUCTION_SURFACES = (
    "**/AGENTS.md",
    "**/CLAUDE.md",
    "**/.claude/**",
    "**/.cursor/rules/**",
    "**/.agents/skills/**",
    "**/.codex/**",
    "**/SKILL.md",
)


def run(context: ScanContext) -> list[Finding]:
    if not verification_active(context):
        return []
    hit = touched(_AGENT_INSTRUCTION_SURFACES, changed_files(context))
    if not hit:
        return []
    return [
        verify_finding(
            context,
            check_id=CHECK_ID,
            title="Agent instructions changed; weakening cannot be ruled out",
            severity="medium",
            evidence={
                "kind": "agent_instructions_changed",
                "changed_instruction_files": hit,
            },
            recommendation=(
                "This PR edits agent-instruction files. Shipgate cannot "
                "statically prove the instructions were not weakened, so a "
                "human must review the change and confirm no gate-protecting "
                "instruction was removed or softened."
            ),
        )
    ]
