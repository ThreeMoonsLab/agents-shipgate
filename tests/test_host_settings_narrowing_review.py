"""A host settings narrowing the lattice decides needs no human review (#661).

Owner decision (2026-09-13): in an adopted repository, a change to a host
settings trust root whose only effect is a narrowing the permission lattice
decides does not end the agent's turn on human review. Anything else keeps
the review: a hook change, another key, or a rule the lattice cannot decide.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.checks import verify
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.agent_boundary import (
    assessment_for_scan_context,
    build_agent_boundary_result,
)
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.schemas.verification import VerificationContext

CLAUDE = ".claude/settings.json"
CURSOR = ".cursor/cli.json"


def _context(tmp_path: Path, relative: str, before: dict, after: dict) -> ScanContext:
    old_text = json.dumps(before, indent=2)
    new_text = json.dumps(after, indent=2)
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(old_text, encoding="utf-8")
    old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
    body = "\n".join([f"-{line}" for line in old_lines] + [f"+{line}" for line in new_lines])
    diff = (
        f"diff --git a/{relative} b/{relative}\nindex 1111111..2222222 100644\n"
        f"--- a/{relative}\n+++ b/{relative}\n"
        f"@@ -1,{len(old_lines)} +1,{len(new_lines)} @@\n{body}\n"
    )
    manifest = load_manifest(
        Path(__file__).resolve().parent.parent / "samples" / "support_refund_agent" / "shipgate.yaml"
    )
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=tmp_path / "shipgate.yaml",
        verification=VerificationContext(
            diff_text=diff, diff_text_available=True, changed_files=[relative]
        ),
    )


NARROWINGS = {
    "allow tightened": (
        CLAUDE,
        {"permissions": {"allow": ["Bash(*)", "Read(**)"]}},
        {"permissions": {"allow": ["Bash(git status)", "Read(**)"]}},
    ),
    "narrower rule beside the wildcard it came from": (
        CLAUDE,
        {"permissions": {"allow": ["Bash(*)"]}},
        {"permissions": {"allow": ["Bash(*)", "Bash(git status)"]}},
    ),
    "allow removed": (
        CLAUDE,
        {"permissions": {"allow": ["Bash(*)", "Read(**)"]}},
        {"permissions": {"allow": ["Read(**)"]}},
    ),
    "deny added": (
        CLAUDE,
        {"permissions": {"allow": ["Read(**)"]}},
        {"permissions": {"allow": ["Read(**)"], "deny": ["Read(.env)"]}},
    ),
    "ask added": (
        CLAUDE,
        {"permissions": {"allow": ["Bash(*)"]}},
        {"permissions": {"allow": ["Bash(*)"], "ask": ["Bash(git push:*)"]}},
    ),
    "rules reordered": (
        CLAUDE,
        {"permissions": {"allow": ["Bash(*)", "Read(**)"]}},
        {"permissions": {"allow": ["Read(**)", "Bash(*)"]}},
    ),
    "cursor allow tightened": (
        CURSOR,
        {"permissions": {"allow": ["Shell(*)"]}},
        {"permissions": {"allow": ["Shell(git status)"]}},
    ),
}

REVIEWED = {
    "hook added beside a narrowing": (
        CLAUDE,
        {"permissions": {"allow": ["Bash(*)"]}},
        {
            "permissions": {"allow": ["Bash(git status)"]},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}]},
        },
    ),
    "another key beside a narrowing": (
        CLAUDE,
        {"model": "sonnet", "permissions": {"allow": ["Bash(*)"]}},
        {"model": "opus", "permissions": {"allow": ["Bash(git status)"]}},
    ),
    "a rule the lattice cannot decide": (
        CLAUDE,
        {"permissions": {"allow": ["Read(src/**)"]}},
        {"permissions": {"allow": ["Read(src/a.py)"]}},
    ),
    "widened": (
        CLAUDE,
        {"permissions": {"allow": ["Read(**)"]}},
        {"permissions": {"allow": ["Read(**)", "WebFetch(*)"]}},
    ),
    "deny removed": (
        CLAUDE,
        {"permissions": {"allow": ["Read(**)"], "deny": ["Read(.env)"]}},
        {"permissions": {"allow": ["Read(**)"]}},
    ),
    "ask removed": (
        CLAUDE,
        {"permissions": {"allow": ["Bash(*)"], "ask": ["Bash(git push:*)"]}},
        {"permissions": {"allow": ["Bash(*)"]}},
    ),
    "default mode set beside a narrowing": (
        CLAUDE,
        {"permissions": {"allow": ["Bash(*)"]}},
        {"permissions": {"allow": ["Bash(git status)"], "defaultMode": "acceptEdits"}},
    ),
    "an ask list that is not a list": (
        CLAUDE,
        {"permissions": {"allow": ["Bash(*)"]}},
        {"permissions": {"allow": ["Bash(git status)"], "ask": "Bash(git push:*)"}},
    ),
    "cursor network changed beside a narrowing": (
        CURSOR,
        {"permissions": {"allow": ["Shell(*)"]}},
        {"permissions": {"allow": ["Shell(git status)"]}, "network": {"allow": ["*"]}},
    ),
}


@pytest.mark.parametrize("case", NARROWINGS, ids=str)
def test_a_decided_narrowing_is_evaluated_and_not_sent_to_a_human(tmp_path: Path, case: str) -> None:
    relative, before, after = NARROWINGS[case]
    context = _context(tmp_path, relative, before, after)

    assessment = assessment_for_scan_context(context)

    assert [row.id for row in assessment.violations if row.path == relative] == []
    assert assessment.host_settings_narrowed == frozenset({relative})
    assert verify.run(context) == []
    # Still on the record: the check result names the narrowing.
    result = build_agent_boundary_result(assessment)
    assert [
        (item.code, item.level, item.path)
        for item in result.diagnostics
        if item.code == "host_settings_narrowed"
    ] == [("host_settings_narrowed", "info", relative)]


@pytest.mark.parametrize("case", REVIEWED, ids=str)
def test_anything_else_keeps_the_review(tmp_path: Path, case: str) -> None:
    relative, before, after = REVIEWED[case]
    context = _context(tmp_path, relative, before, after)

    assessment = assessment_for_scan_context(context)

    assert assessment.host_settings_narrowed == frozenset()
    assert [row for row in assessment.violations if row.path == relative], case
    assert [finding.check_id for finding in verify.run(context)] == [
        "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
    ]
