"""Public-surface contract drift tests.

Catches the most common adoption blocker for an agent-friendly repo:
agent-facing files (skill, slash command, llms.txt, .well-known, FAQ,
prompts, examples) drifting away from the contract documented in
STABILITY.md and docs/agent-contract-current.md.

Failure here means an AI coding agent reading the file would receive
stale guidance — e.g. recommending `summary.status` as a release-gating
field or pointing at a frozen-reference schema as 'current'.

Single source of truth: docs/agent-contract-current.md. Runtime version
constants come from the same models that generate schemas/reports; when
the contract bumps, update STABILITY.md and the keystone doc first, then
walk PUBLIC_SURFACES.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agents_shipgate import __version__
from agents_shipgate.contract import (
    CONTRACT_VERSION,
    GATING_SIGNAL,
    build_contract_payload,
)
from agents_shipgate.core.models import ReadinessReport
from agents_shipgate.packet.models import EvidencePacket

REPO_ROOT = Path(__file__).resolve().parent.parent

CURRENT_REPORT_SCHEMA_VERSION = str(
    ReadinessReport.model_fields["report_schema_version"].default
)
CURRENT_REPORT_SCHEMA = f"report-schema.v{CURRENT_REPORT_SCHEMA_VERSION}.json"
CURRENT_PACKET_SCHEMA_VERSION = str(
    EvidencePacket.model_fields["packet_schema_version"].default
)
CURRENT_PACKET_SCHEMA = f"packet-schema.v{CURRENT_PACKET_SCHEMA_VERSION}.json"
# v0.9 became a frozen reference once main shipped v0.10 (tool-surface diff).
LEGACY_REPORT_SCHEMA_PATTERN = re.compile(r"report-schema\.v0\.(?:7|8|9)\.json")
ANY_REPORT_SCHEMA_PATTERN = re.compile(r"report-schema\.v0\.\d+\.json")
ANY_PACKET_SCHEMA_PATTERN = re.compile(r"packet-schema\.v\d+\.\d+\.json")
SUMMARY_STATUS_PATTERN = re.compile(
    r"summary\.status\b|summary\.\{[^}]*status[^}]*\}"
)
LEGACY_CONTEXT_WORDS = re.compile(
    r"\b(?:frozen|legacy|compat|compatibility|baseline-blind|preserved|"
    r"older|pre-v|kept for|v0\.7 caller|previously)\b",
    re.IGNORECASE,
)
CURRENT_CONTEXT_WORDS = re.compile(r"\bcurrent\b", re.IGNORECASE)
CONTEXT_WINDOW = 400  # ~one paragraph; tight enough that the original
                       # stale `.claude/commands/shipgate.md` (no legacy
                       # marker for hundreds of chars) would still fail.

# The public agent surface. A coding agent reading any of these decides
# how to integrate; drift here directly causes adoption regressions.
PUBLIC_SURFACES = (
    "README.md",
    "AGENTS.md",
    "llms.txt",
    ".well-known/agents-shipgate.json",
    "skills/agents-shipgate/SKILL.md",
    ".claude/commands/shipgate.md",
    "prompts/add-shipgate-to-repo.md",
    "docs/faq.md",
    "examples/github-actions/README.md",
    "docs/agent-contract-current.md",
)


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


def _has_legacy_context(text: str, start: int, end: int) -> bool:
    snippet = text[max(0, start - CONTEXT_WINDOW): end + CONTEXT_WINDOW]
    return bool(LEGACY_CONTEXT_WORDS.search(snippet))


def _has_current_context(text: str, start: int, end: int) -> bool:
    snippet = text[max(0, start - CONTEXT_WINDOW): end + CONTEXT_WINDOW]
    return bool(CURRENT_CONTEXT_WORDS.search(snippet))


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES)
def test_public_surface_mentions_current_schema_when_it_mentions_any(relpath):
    """A file that talks about report schemas at all must talk about
    the current one. Files that don't mention schemas are fine."""
    text = _read(relpath)
    if not ANY_REPORT_SCHEMA_PATTERN.search(text):
        return
    assert CURRENT_REPORT_SCHEMA in text, (
        f"{relpath} references a report schema but not the current one "
        f"({CURRENT_REPORT_SCHEMA}). Update accordingly — see "
        "docs/agent-contract-current.md."
    )


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES)
def test_public_surface_does_not_mark_old_packet_schema_current(relpath):
    """Packet schema references marked as current must follow the live
    EvidencePacket version, not a hand-maintained literal."""
    text = _read(relpath)
    for match in ANY_PACKET_SCHEMA_PATTERN.finditer(text):
        if match.group(0) == CURRENT_PACKET_SCHEMA:
            continue
        assert not _has_current_context(text, match.start(), match.end()), (
            f"{relpath} marks {match.group(0)!r} as current, but the "
            f"runtime packet schema is {CURRENT_PACKET_SCHEMA!r}."
        )


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES)
def test_public_surface_marks_legacy_schemas_as_frozen(relpath):
    """Older schemas may appear (frozen-reference table, migration
    notes), but only when a 'frozen / legacy / compat / older' marker
    sits within ~200 chars."""
    text = _read(relpath)
    for match in LEGACY_REPORT_SCHEMA_PATTERN.finditer(text):
        assert _has_legacy_context(text, match.start(), match.end()), (
            f"{relpath} mentions {match.group(0)!r} without a clearly "
            "legacy / frozen / compat marker nearby. Either drop the "
            "reference or label it (see AGENTS.md schema table for "
            "the canonical phrasing)."
        )


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES)
def test_public_surface_does_not_recommend_summary_status_for_gating(relpath):
    """`summary.status` is baseline-blind and preserved only for v0.7
    callers. New gating instructions must lead with
    `release_decision.decision`. Mentions of `summary.status` are
    allowed when paired with a legacy/compat/baseline-blind marker."""
    text = _read(relpath)
    for match in SUMMARY_STATUS_PATTERN.finditer(text):
        assert _has_legacy_context(text, match.start(), match.end()), (
            f"{relpath} mentions {match.group(0)!r} without a 'legacy / "
            "baseline-blind / v0.7 compat' marker nearby. Lead with "
            "`release_decision.decision` for any new gating instruction."
        )


def test_well_known_metadata_lists_packet_outputs():
    """packet.{md,json,html} are first-class outputs per
    STABILITY.md §Release Evidence Packet — discovery metadata must
    reflect that so coding agents know to surface them."""
    data = json.loads(_read(".well-known/agents-shipgate.json"))
    contract = build_contract_payload().model_dump(mode="json")
    assert data.get("contract") == "agents-shipgate contract --json"
    assert data.get("contract_version") == contract["contract_version"]
    assert data.get("version") == contract["cli_version"]
    package = data.get("package", {})
    assert package.get("github_action") == (
        f"ThreeMoonsLab/agents-shipgate@v{contract['cli_version']}"
    )
    outputs = data.get("outputs", [])
    for expected in ("packet_md", "packet_json", "packet_html"):
        assert expected in outputs, (
            f".well-known/agents-shipgate.json outputs missing {expected!r}; "
            "the Release Evidence Packet is first-class since v0.8."
        )
    schemas = data.get("schemas", {})
    assert "packet" in schemas, (
        ".well-known/agents-shipgate.json `schemas` missing 'packet'; "
        "expected a URL pointing to the current packet schema "
        f"(docs/{CURRENT_PACKET_SCHEMA})."
    )
    assert data.get("gating_signal") == contract["gating_signal"], (
        ".well-known/agents-shipgate.json must declare "
        "gating_signal: 'release_decision.decision' so coding agents "
        "don't fall back to summary.status."
    )
    report_url = schemas.get("report", "")
    assert CURRENT_REPORT_SCHEMA in report_url, (
        f".well-known schemas.report must point to {CURRENT_REPORT_SCHEMA}; "
        f"got {report_url!r}."
    )
    packet_url = schemas.get("packet", "")
    assert CURRENT_PACKET_SCHEMA in packet_url, (
        f".well-known schemas.packet must point to {CURRENT_PACKET_SCHEMA}; "
        f"got {packet_url!r}."
    )


def test_agent_contract_current_doc_is_canonical():
    """docs/agent-contract-current.md is the keystone — when the
    contract bumps it updates first. Pin its essentials so it cannot
    silently drift."""
    text = _read("docs/agent-contract-current.md")
    contract = build_contract_payload().model_dump(mode="json")
    assert "agents-shipgate contract --json" in text, (
        "docs/agent-contract-current.md must tell agents how to verify "
        "the installed runtime contract locally."
    )
    assert f"Runtime contract: `{CONTRACT_VERSION}`" in text, (
        "docs/agent-contract-current.md must mention the current runtime "
        f"contract version `{CONTRACT_VERSION}`."
    )
    assert __version__ == contract["cli_version"]
    assert f"Latest release: `v{contract['cli_version']}`" in text, (
        "docs/agent-contract-current.md must agree with the runtime "
        "contract's cli_version."
    )
    assert CURRENT_REPORT_SCHEMA in text, (
        "docs/agent-contract-current.md must reference the current "
        f"report schema ({CURRENT_REPORT_SCHEMA})."
    )
    assert f"`{CURRENT_REPORT_SCHEMA_VERSION}`" in text, (
        "docs/agent-contract-current.md must mention the current "
        f"version string `{CURRENT_REPORT_SCHEMA_VERSION}`."
    )
    assert GATING_SIGNAL in text, (
        "docs/agent-contract-current.md must lead with "
        "release_decision.decision as the gating signal."
    )
    assert "manual_review_signals[]" in text, (
        "docs/agent-contract-current.md must mention the local contract's "
        "manual_review_signals[] field."
    )
    assert CURRENT_PACKET_SCHEMA in text, (
        "docs/agent-contract-current.md must reference the current packet "
        f"schema (v{CURRENT_PACKET_SCHEMA_VERSION}) so coding agents know "
        "about the Release Evidence Packet."
    )


def test_action_pr_comment_uses_sticky_marker():
    """The GitHub Action PR comment must upsert via a sticky marker
    rather than appending new comments on every scan — re-runs would
    otherwise spam the PR. The marker also lets external tooling find
    Shipgate's comment programmatically."""
    text = (REPO_ROOT / "action.yml").read_text(encoding="utf-8")
    assert "<!-- agents-shipgate-pr-comment -->" in text, (
        "action.yml PR comment script must embed the "
        "<!-- agents-shipgate-pr-comment --> sticky marker."
    )
    assert "updateComment" in text, (
        "action.yml PR comment script must call updateComment when a "
        "prior sticky-marked comment exists (upsert, not append)."
    )
