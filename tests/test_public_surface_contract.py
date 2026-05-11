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

import importlib.util
import json
import re
import tomllib
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
from agents_shipgate.triggers import evaluate, load_triggers

REPO_ROOT = Path(__file__).resolve().parent.parent

CURRENT_REPORT_SCHEMA_VERSION = str(
    ReadinessReport.model_fields["report_schema_version"].default
)
CURRENT_REPORT_SCHEMA = f"report-schema.v{CURRENT_REPORT_SCHEMA_VERSION}.json"
CURRENT_PACKET_SCHEMA_VERSION = str(
    EvidencePacket.model_fields["packet_schema_version"].default
)
CURRENT_PACKET_SCHEMA = f"packet-schema.v{CURRENT_PACKET_SCHEMA_VERSION}.json"
# v0.11 became a frozen reference once main shipped v0.12
# (per-finding agent_action + top-level agent_summary).
LEGACY_REPORT_SCHEMA_PATTERN = re.compile(r"report-schema\.v0\.(?:7|8|9|10|11)\.json")
ANY_REPORT_SCHEMA_PATTERN = re.compile(r"report-schema\.v0\.\d+\.json")
ANY_PACKET_SCHEMA_PATTERN = re.compile(r"packet-schema\.v\d+\.\d+\.json")
LEGACY_PACKET_SCHEMA_PATTERN = re.compile(r"packet-schema\.v0\.(?:1|2)\.json")
PACKET_ANCHOR_PATTERN = re.compile(r"#release-evidence-packet-v(\d+)")
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

ACTION_PIN_PATTERN = re.compile(
    r"ThreeMoonsLab/agents-shipgate@v(\d+\.\d+\.\d+)"
)
PIP_PIN_PATTERN = re.compile(r"agents-shipgate==(\d+\.\d+\.\d+)")
SHIPGATE_VERSION_INPUT_PATTERN = re.compile(
    r"shipgate_version:\s*['\"](\d+\.\d+\.\d+)['\"]"
)
# Surfaces that name the *latest released* version inline (not as an
# Action / pip / shipgate_version pin) and must move with the package
# version on every bump. Each entry is a (path, regex) pair where the
# regex's first capture group is the version literal to compare against
# pyproject.toml. The regexes are anchored to surrounding phrasing so
# historical version mentions in the same file (e.g. ROADMAP.md's
# release-history list, faq.md's older v0.x narrative) are not matched.
VERSION_LITERAL_TARGETS = (
    (
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        re.compile(r"placeholder:\s*\"v(\d+\.\d+\.\d+)\""),
    ),
    (
        "docs/distribution.md",
        re.compile(
            r"Pinned GitHub Action release tags[^\n]*?including\s+`v(\d+\.\d+\.\d+)`"
        ),
    ),
    (
        "docs/faq.md",
        re.compile(r"v(\d+\.\d+\.\d+) is the latest released version"),
    ),
    (
        "ROADMAP.md",
        re.compile(r"preparing the\s+`v(\d+\.\d+\.\d+)`\s+release"),
    ),
)
# Forbidden public/display forms. Word boundaries on both sides keep
# "Agents Shipgate" (canonical) from matching.
FORBIDDEN_NAME_PATTERN = re.compile(
    r"(?<![A-Za-z])Agent\s+(?:Shipcheck|Shipgate)(?![A-Za-z])"
)
DO_NOT_USE_CONTEXT_PATTERN = re.compile(
    r"do\s*\*{0,2}\s*not\s*\*{0,2}\s*use|avoid these names|forbidden",
    re.IGNORECASE,
)

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

# Strict superset of PUBLIC_SURFACES that adds files which carry
# version pins (Action `@vX.Y.Z`, pip `==X.Y.Z`, or `shipgate_version:`).
# `marketing/linkedin-launch-post.md` is intentionally excluded — frozen
# launch copy is allowed to reference historic releases (e.g. v0.5.1).
# Schema files (`docs/{report,packet}-schema.v0.X.json`) are excluded
# because their `$id` necessarily names their own frozen version.
ACTION_PIN_FILES = (
    *PUBLIC_SURFACES,
    "docs/integrations.md",
    "docs/quickstart.md",
    "docs/target-repo-agent-snippets.md",
    "examples/github-actions/01-advisory-pr-comment.yml",
    "examples/github-actions/02-strict-on-critical.yml",
    "examples/github-actions/03-strict-with-baseline.yml",
    "examples/github-actions/04-multi-config-workspace.yml",
    "examples/github-actions/05-sarif-to-code-scanning.yml",
    "examples/github-actions/06-on-tool-source-changes.yml",
    "examples/circleci/01-advisory.yml",
    "examples/circleci/02-strict-with-baseline.yml",
    "examples/circleci/03-sarif-artifact-retention.yml",
    "examples/circleci/04-multi-config-workspace.yml",
    "examples/circleci/05-on-tool-source-changes.yml",
    "examples/gitlab-ci/01-advisory.yml",
    "examples/gitlab-ci/02-strict-with-baseline.yml",
    "examples/gitlab-ci/03-sarif-or-artifact.yml",
    "examples/gitlab-ci/04-multi-config-workspace.yml",
    "examples/gitlab-ci/05-on-tool-source-changes.yml",
    "prompts/stabilize-strict-mode.md",
    "skills/agents-shipgate/prompts/stabilize-strict-mode.md",
    "skills/agents-shipgate/ci-recipes/advisory-pr-comment.yml",
)


def _load_pyproject_version() -> str:
    """Read `[project].version` from pyproject.toml — single source of
    truth for the package version that every public surface must echo."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


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


# --- Drift guards: schema versions and constants vs. contract doc ----------


def test_constants_match_contract_doc():
    """The in-test constants (CURRENT_REPORT_SCHEMA_VERSION,
    CURRENT_PACKET_SCHEMA_VERSION) must agree with what
    docs/agent-contract-current.md declares. Bumping a schema means
    bumping the contract doc *and* this test's constants — both
    must move together."""
    text = _read("docs/agent-contract-current.md")
    report_match = re.search(
        r"Current report schema:\s*`(\d+\.\d+)`", text
    )
    packet_match = re.search(
        r"Current packet schema:\s*`(\d+\.\d+)`", text
    )
    release_match = re.search(
        r"Latest release:\s*`v(\d+\.\d+\.\d+)`", text
    )
    assert report_match, (
        "docs/agent-contract-current.md must declare 'Current report "
        "schema: `X.Y`' so the test constants can be cross-checked."
    )
    assert packet_match, (
        "docs/agent-contract-current.md must declare 'Current packet "
        "schema: `X.Y`'."
    )
    assert release_match, (
        "docs/agent-contract-current.md must declare 'Latest release: "
        "`vX.Y.Z`'."
    )
    assert report_match.group(1) == CURRENT_REPORT_SCHEMA_VERSION, (
        f"contract doc says report schema is "
        f"{report_match.group(1)!r}; test constant says "
        f"{CURRENT_REPORT_SCHEMA_VERSION!r}. Update both together."
    )
    assert packet_match.group(1) == CURRENT_PACKET_SCHEMA_VERSION, (
        f"contract doc says packet schema is "
        f"{packet_match.group(1)!r}; test constant says "
        f"{CURRENT_PACKET_SCHEMA_VERSION!r}. Update both together."
    )
    assert release_match.group(1) == _load_pyproject_version(), (
        f"contract doc says latest release is "
        f"v{release_match.group(1)}; pyproject.toml says "
        f"v{_load_pyproject_version()}. Update both together."
    )


def test_pyproject_version_propagates_to_metadata_surfaces():
    """pyproject.toml [project].version is the single source of truth
    for the package version. Every public metadata surface must echo it
    exactly. Catches a stale .well-known, llms.txt, contract doc, or
    src/__init__ when the package version bumps."""
    expected = _load_pyproject_version()

    # src/agents_shipgate/__init__.__version__
    import agents_shipgate

    assert agents_shipgate.__version__ == expected, (
        f"agents_shipgate.__version__ is "
        f"{agents_shipgate.__version__!r}; pyproject.toml says "
        f"{expected!r}. Update src/agents_shipgate/__init__.py."
    )

    # .well-known/agents-shipgate.json
    well_known = json.loads(_read(".well-known/agents-shipgate.json"))
    assert well_known["version"] == expected, (
        f".well-known/agents-shipgate.json `version` is "
        f"{well_known['version']!r}; pyproject.toml says "
        f"{expected!r}."
    )
    action_pin = well_known["package"]["github_action"]
    action_match = ACTION_PIN_PATTERN.search(action_pin)
    assert action_match, (
        f".well-known package.github_action {action_pin!r} does not "
        "match the expected ThreeMoonsLab/agents-shipgate@vX.Y.Z form."
    )
    assert action_match.group(1) == expected, (
        f".well-known package.github_action pins "
        f"v{action_match.group(1)}; pyproject.toml says v{expected}."
    )

    # llms.txt — both the "Latest public release" line and the
    # GitHub Action line must echo the package version.
    llms_text = _read("llms.txt")
    llms_release = re.search(
        r"Latest public release:\s*v(\d+\.\d+\.\d+)", llms_text
    )
    assert llms_release, (
        "llms.txt must declare 'Latest public release: vX.Y.Z'."
    )
    assert llms_release.group(1) == expected, (
        f"llms.txt 'Latest public release' is "
        f"v{llms_release.group(1)}; pyproject.toml says v{expected}."
    )
    llms_action = ACTION_PIN_PATTERN.search(llms_text)
    assert llms_action, (
        "llms.txt must include a ThreeMoonsLab/agents-shipgate@vX.Y.Z "
        "Action pin so coding agents know the canonical version."
    )
    assert llms_action.group(1) == expected, (
        f"llms.txt Action pin is v{llms_action.group(1)}; "
        f"pyproject.toml says v{expected}."
    )

    # docs/agent-contract-current.md
    contract_text = _read("docs/agent-contract-current.md")
    contract_release = re.search(
        r"Latest release:\s*`v(\d+\.\d+\.\d+)`", contract_text
    )
    assert contract_release and contract_release.group(1) == expected, (
        f"docs/agent-contract-current.md 'Latest release' must be "
        f"`v{expected}`."
    )


def _file_lines_with_pin(path: str, pattern: re.Pattern[str]):
    """Yield (line_number, line_text, captured_version) for every
    pin-pattern hit in the file. Empty if the file has no pins."""
    text = _read(path)
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            yield line_number, line, match.group(1)


@pytest.mark.parametrize("relpath", ACTION_PIN_FILES)
def test_action_pins_match_pyproject_version(relpath):
    """Every `ThreeMoonsLab/agents-shipgate@vX.Y.Z` pin in a public
    surface must equal the package version. Catches stale Action pins
    that point at a tag that doesn't exist (e.g., @v0.10.0 before the
    bump) or that lag behind the bump."""
    expected = _load_pyproject_version()
    for line_number, line, found in _file_lines_with_pin(
        relpath, ACTION_PIN_PATTERN
    ):
        assert found == expected, (
            f"{relpath}:{line_number} pins "
            f"ThreeMoonsLab/agents-shipgate@v{found}; pyproject.toml "
            f"says v{expected}. Update the pin to @v{expected} or "
            f"bump pyproject.toml.\n  line: {line.strip()!r}"
        )


@pytest.mark.parametrize("relpath", ACTION_PIN_FILES)
def test_pip_pins_match_pyproject_version(relpath):
    """Every `agents-shipgate==X.Y.Z` install pin in a public surface
    must equal the package version. Same drift guard as the Action
    pin test, for pip-based CI examples."""
    expected = _load_pyproject_version()
    for line_number, line, found in _file_lines_with_pin(
        relpath, PIP_PIN_PATTERN
    ):
        assert found == expected, (
            f"{relpath}:{line_number} pins agents-shipgate=={found}; "
            f"pyproject.toml says {expected}. Update the pin to "
            f"=={expected} or bump pyproject.toml.\n  line: "
            f"{line.strip()!r}"
        )


@pytest.mark.parametrize("relpath", ACTION_PIN_FILES)
def test_shipgate_version_inputs_match_pyproject_version(relpath):
    """The `shipgate_version: '<version>'` Action input in workflow
    examples must match the package version too. Catches a stale
    matrix where the Action pin is updated but the CLI install
    version inside it is left behind."""
    expected = _load_pyproject_version()
    for line_number, line, found in _file_lines_with_pin(
        relpath, SHIPGATE_VERSION_INPUT_PATTERN
    ):
        assert found == expected, (
            f"{relpath}:{line_number} sets shipgate_version: "
            f"'{found}'; pyproject.toml says {expected}.\n  line: "
            f"{line.strip()!r}"
        )


@pytest.mark.parametrize("relpath,pattern", VERSION_LITERAL_TARGETS)
def test_version_literals_match_pyproject_version(relpath, pattern):
    """Plain release-version literals on these public surfaces (the
    bug-report placeholder, distribution.md's release-tag list,
    faq.md's 'latest released version' line, ROADMAP.md's lead
    paragraph) must move with pyproject.toml on every bump. The
    Action / pip / shipgate_version pin tests don't catch these
    because the literals aren't pins."""
    expected = _load_pyproject_version()
    text = _read(relpath)
    match = pattern.search(text)
    assert match, (
        f"{relpath} no longer contains the expected version-literal "
        f"phrase ({pattern.pattern!r}). Either the surface was rewritten "
        "(update VERSION_LITERAL_TARGETS to match the new phrasing) or "
        "the literal was dropped entirely."
    )
    assert match.group(1) == expected, (
        f"{relpath} names release version v{match.group(1)} in "
        f"public copy; pyproject.toml says v{expected}. Bump the "
        "literal in this file or align pyproject.toml.\n  match: "
        f"{match.group(0)!r}"
    )


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES)
def test_public_surface_mentions_current_packet_schema_when_it_mentions_any(
    relpath,
):
    """A file that talks about packet schemas at all must talk about
    the current one. Files that don't mention packet schemas are fine.
    Packet-schema analogue of the existing report-schema check."""
    text = _read(relpath)
    if not ANY_PACKET_SCHEMA_PATTERN.search(text):
        return
    assert CURRENT_PACKET_SCHEMA in text, (
        f"{relpath} references a packet schema but not the current one "
        f"({CURRENT_PACKET_SCHEMA}). Update accordingly — see "
        "docs/agent-contract-current.md."
    )


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES)
def test_public_surface_marks_legacy_packet_schemas_as_frozen(relpath):
    """Older packet schemas may appear (frozen-reference notes,
    migration), but only when a 'frozen / legacy / compat / older'
    marker sits within ~one paragraph. Mirrors the existing
    report-schema legacy check."""
    text = _read(relpath)
    for match in LEGACY_PACKET_SCHEMA_PATTERN.finditer(text):
        assert _has_legacy_context(text, match.start(), match.end()), (
            f"{relpath} mentions {match.group(0)!r} without a clearly "
            "legacy / frozen / compat marker nearby. Either drop the "
            "reference or label it (see AGENTS.md schemas table for "
            "the canonical phrasing)."
        )


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES)
def test_packet_anchors_match_current_schema(relpath):
    """`#release-evidence-packet-vXX` anchors in agent-facing surfaces
    must match the current packet schema version (e.g., v0.3 →
    `v03`). Catches anchor typos like `#release-evidence-packet-v01`
    that quietly point at a non-existent STABILITY.md section."""
    text = _read(relpath)
    expected_anchor_digits = CURRENT_PACKET_SCHEMA_VERSION.replace(".", "")
    for match in PACKET_ANCHOR_PATTERN.finditer(text):
        assert match.group(1) == expected_anchor_digits, (
            f"{relpath} contains anchor "
            f"`#release-evidence-packet-v{match.group(1)}`; "
            f"current packet schema is "
            f"v{CURRENT_PACKET_SCHEMA_VERSION}, so the anchor should "
            f"be `#release-evidence-packet-v{expected_anchor_digits}`."
        )


# --- Trigger catalog and llms-full.txt drift guards ----------------------


_VALID_TRIGGER_ACTIONS = {"run_shipgate", "skip_shipgate", "dry_run", "force_run"}


def _load_triggers_json() -> dict:
    return json.loads(_read("docs/triggers.json"))


def test_triggers_json_loads_via_canonical_loader():
    """The bundled `agents_shipgate.triggers` module is the canonical
    loader. If a coding agent reads docs/triggers.json directly and
    reaches a different verdict than this loader, that's a drift bug —
    catch it by exercising the loader during CI."""
    triggers = load_triggers()
    assert triggers["schema_version"] == "0.1", (
        "docs/triggers.json schema_version moved off 0.1; bump the "
        "test constant deliberately so external consumers are notified."
    )
    assert isinstance(triggers.get("rules"), list) and triggers["rules"], (
        "docs/triggers.json must declare at least one rule."
    )
    for rule in triggers["rules"]:
        assert rule["action"] in _VALID_TRIGGER_ACTIONS, (
            f"rule {rule['id']!r} has unknown action {rule['action']!r}; "
            f"allowed: {sorted(_VALID_TRIGGER_ACTIONS)}."
        )
        assert rule.get("when"), f"rule {rule['id']!r} missing `when` clause."
        assert rule.get("agents_md_row"), (
            f"rule {rule['id']!r} missing `agents_md_row`; the row text "
            "is what the contract test pins against AGENTS.md prose."
        )


def test_triggers_json_rule_rows_appear_verbatim_in_agents_md():
    """Every `agents_md_row` value in docs/triggers.json must appear
    verbatim in AGENTS.md. Catches the failure mode where the prose
    table gets reworded but triggers.json is left behind."""
    triggers = _load_triggers_json()
    agents_md = _read("AGENTS.md")
    seen: set[str] = set()
    for rule in triggers["rules"]:
        row = rule["agents_md_row"]
        if row in seen:
            continue
        seen.add(row)
        assert row in agents_md, (
            f"rule {rule['id']!r} declares agents_md_row "
            f"{row!r}, but that text does not appear verbatim in "
            "AGENTS.md. Re-sync docs/triggers.json and the AGENTS.md "
            "trigger table."
        )


def test_triggers_evaluator_smoke():
    """Sanity-check the evaluator for the canonical positive and
    negative cases. Prevents a regression where rule semantics drift
    silently — e.g. the docs-only negative case starts firing
    `run_shipgate`."""
    docs_only = evaluate(paths=["README.md", "docs/index.md"])
    assert docs_only["run_shipgate"] is False, (
        "Docs-only PR must not trigger Shipgate; "
        f"got {docs_only!r}."
    )
    mcp_change = evaluate(paths=["tools/my_mcp.json"])
    assert mcp_change["run_shipgate"] is True, (
        "MCP export change must trigger Shipgate; "
        f"got {mcp_change!r}."
    )
    decorator = evaluate(
        paths=["agent.py"],
        diff_text="+@function_tool\n+def search(): ...",
    )
    assert decorator["run_shipgate"] is True, (
        "@function_tool decorator addition must trigger Shipgate; "
        f"got {decorator!r}."
    )


def test_triggers_skip_beats_run_on_docs_only_with_decorator_in_prose():
    """A README-only diff that incidentally mentions `@tool` (e.g.
    documentation prose, a code block, or a quoted Action URL) must
    NOT trigger Shipgate. `skip_shipgate` beats `run_shipgate`;
    otherwise the docs-only-negative rule is dead in practice."""
    result = evaluate(
        paths=["README.md"],
        diff_text="+ Use @tool to register handlers (see ThreeMoonsLab/agents-shipgate)",
    )
    assert result["run_shipgate"] is False, (
        "Docs-only PR with prose-mentioned @tool must NOT trigger "
        f"Shipgate; got {result!r}."
    )
    matched_actions = {m["action"] for m in result["matched_rules"]}
    assert "skip_shipgate" in matched_actions, (
        "Expected the docs-only negative rule to fire alongside the "
        "decorator/Action rules; otherwise the precedence isn't being "
        f"exercised. Got matched_rules={result['matched_rules']!r}."
    )


@pytest.mark.parametrize(
    "paths",
    [
        ["tests/test_foo.py"],
        ["tests/conftest.py"],
        ["src/pkg/test_module.py"],
        ["src/pkg/module_test.py"],
        ["tests/test_a.py", "tests/test_b.py", "tests/conftest.py"],
        ["README.md", "tests/test_foo.py", "docs/index.md"],
    ],
    ids=[
        "single-test-file",
        "conftest",
        "test-prefix-py",
        "test-suffix-py",
        "multi-test",
        "mixed-docs-and-tests",
    ],
)
def test_triggers_test_only_diff_with_decorator_skips(paths):
    """Test-only diffs (or test+doc diffs) that incidentally contain
    `@function_tool` in fixtures or assertions must skip — the
    AGENTS.md row says "Pure read-only doc/test changes" and the
    catalog must honor 'test'. Catches a regression where the rule
    only matches `**/*.md` and tests slip through."""
    result = evaluate(
        paths=paths,
        diff_text=(
            "+@function_tool\n+def stub(): pass  # used in fixtures"
        ),
    )
    assert result["run_shipgate"] is False, (
        f"Test-only paths {paths!r} with @function_tool in diff must "
        f"NOT trigger Shipgate; got {result!r}."
    )
    matched_ids = {m["id"] for m in result["matched_rules"]}
    assert "TRIGGER-DOCS-ONLY-NEGATIVE" in matched_ids, (
        "Expected TRIGGER-DOCS-ONLY-NEGATIVE to fire on test-only "
        f"PR; got matched_rules={result['matched_rules']!r}."
    )


def test_triggers_code_plus_test_does_not_skip():
    """A PR that mixes a real code change with a test file is NOT
    test-only and should follow the run rules. Negative case for the
    docs-only-negative rule's `every_file_matches` list expansion."""
    result = evaluate(
        paths=["src/agent.py", "tests/test_agent.py"],
        diff_text="+@function_tool\n+def search(): ...",
    )
    assert result["run_shipgate"] is True, (
        "Code+test mix with @function_tool must trigger Shipgate; "
        f"got {result!r}."
    )
    matched_ids = {m["id"] for m in result["matched_rules"]}
    assert "TRIGGER-DOCS-ONLY-NEGATIVE" not in matched_ids, (
        "TRIGGER-DOCS-ONLY-NEGATIVE must NOT fire when a non-doc, "
        f"non-test file is in the change set; got {result!r}."
    )


def test_every_file_matches_predicate_accepts_list():
    """The `every_file_matches` predicate must accept either a string
    or a list (any-of within the predicate). Pin the contract so a
    refactor doesn't silently revert to string-only."""
    from agents_shipgate.triggers import _eval_predicate

    # Single glob (string form)
    assert _eval_predicate(
        {"every_file_matches": "**/*.md"},
        paths=["README.md", "docs/x.md"],
        diff_text="",
        manifest_present=False,
        detect_result=None,
        user_requested=False,
    ) is True

    # List form: every path matches at least one glob in the list
    assert _eval_predicate(
        {"every_file_matches": ["**/*.md", "tests/**"]},
        paths=["README.md", "tests/test_foo.py"],
        diff_text="",
        manifest_present=False,
        detect_result=None,
        user_requested=False,
    ) is True

    # List form: a path matching no glob in the list returns False
    assert _eval_predicate(
        {"every_file_matches": ["**/*.md", "tests/**"]},
        paths=["README.md", "src/agent.py"],
        diff_text="",
        manifest_present=False,
        detect_result=None,
        user_requested=False,
    ) is False


def test_triggers_force_run_beats_skip_when_manifest_present():
    """A docs-only PR in a repo that already has a `shipgate.yaml`
    must STILL trigger Shipgate — the manifest's existence is the
    operational opt-in, and `force_run` overrides any incidental
    `skip_shipgate` match."""
    result = evaluate(paths=["README.md"], manifest_present=True)
    assert result["run_shipgate"] is True, (
        "Docs-only PR with manifest present must trigger Shipgate "
        f"via TRIGGER-EXISTING-MANIFEST-PRESENT; got {result!r}."
    )
    matched_actions = {m["action"] for m in result["matched_rules"]}
    assert "force_run" in matched_actions, (
        "Expected force_run action to fire when shipgate.yaml is "
        f"present; got matched_rules={result['matched_rules']!r}."
    )


def test_triggers_dry_run_sets_dry_run_recommended():
    """A framework version bump (only `dry_run` rule fires) must
    surface `dry_run_recommended: true` instead of being reported as
    'no rules matched'. Otherwise the dry_run rule is dead in
    practice."""
    result = evaluate(
        paths=["requirements.txt"],
        diff_text="-langchain==0.2.0\n+langchain==0.3.0\n",
    )
    assert result["run_shipgate"] is False, (
        f"dry_run alone should not flip run_shipgate; got {result!r}."
    )
    assert result["dry_run_recommended"] is True, (
        f"Expected dry_run_recommended=True; got {result!r}."
    )
    matched_ids = {m["id"] for m in result["matched_rules"]}
    assert "TRIGGER-FRAMEWORK-VERSION-BUMP" in matched_ids, (
        "Expected TRIGGER-FRAMEWORK-VERSION-BUMP in matched_rules so "
        "callers can see the rationale; got "
        f"matched_rules={result['matched_rules']!r}."
    )


def _init_git_repo(tmp_path: Path) -> None:
    """Initialize an empty git repo at `tmp_path` with one commit so
    `git diff HEAD` works. Used by the --git-diff helper tests."""
    import subprocess

    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path),
         "-c", "user.email=test@example.com",
         "-c", "user.name=test",
         "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
    )


def test_git_diff_bare_includes_staged_changes(tmp_path, monkeypatch):
    """Bare `--git-diff` (no revspec) must capture staged changes via
    `git diff HEAD`. The earlier implementation used plain `git diff`,
    which only sees unstaged changes — a staged `@function_tool`
    addition would silently miss the decorator rule even though the
    prompt advertises bare flag for 'uncommitted changes'."""
    import subprocess

    from agents_shipgate.triggers import _git_diff_context

    _init_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent.py").write_text(
        "@function_tool\ndef foo(): pass\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "agent.py"], check=True)

    paths, diff_text = _git_diff_context(None)
    assert "agent.py" in paths, (
        f"Staged file missing from --git-diff paths: {paths!r}"
    )
    assert "@function_tool" in diff_text, (
        f"Staged content missing from --git-diff diff_text: {diff_text!r}"
    )


def test_git_diff_bare_includes_untracked_paths(tmp_path, monkeypatch):
    """Bare `--git-diff` must surface untracked file paths so that
    glob rules (e.g. `**/*mcp*.json`) can fire on a brand-new file
    the user hasn't `git add`ed yet. Untracked file *content* is NOT
    in the diff body — that limitation is documented in the prompt."""
    from agents_shipgate.triggers import _git_diff_context

    _init_git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "new_mcp.json").write_text('{"tools":[]}', encoding="utf-8")

    paths, diff_text = _git_diff_context(None)
    assert "new_mcp.json" in paths, (
        f"Untracked file missing from --git-diff paths: {paths!r}"
    )
    assert "new_mcp.json" not in diff_text, (
        "Untracked file content must NOT appear in diff_text "
        f"(by design — see prompt's documented limitation); got "
        f"{diff_text!r}"
    )


def test_triggers_existing_manifest_rule_uses_force_run():
    """Pin the action of `TRIGGER-EXISTING-MANIFEST-PRESENT` to
    `force_run` (not `run_shipgate`). Reverting this in triggers.json
    would silently re-introduce the bug where a docs-only PR in an
    opted-in repo gets skipped."""
    triggers = _load_triggers_json()
    rule = next(
        (r for r in triggers["rules"] if r["id"] == "TRIGGER-EXISTING-MANIFEST-PRESENT"),
        None,
    )
    assert rule is not None, (
        "TRIGGER-EXISTING-MANIFEST-PRESENT must remain in the catalog."
    )
    assert rule["action"] == "force_run", (
        "TRIGGER-EXISTING-MANIFEST-PRESENT must use action='force_run' "
        "so it overrides skip_shipgate. The semantics rely on this "
        f"specific action; got action={rule['action']!r}."
    )


def test_well_known_links_to_triggers_and_llms_full():
    """`.well-known/agents-shipgate.json` is the discovery hub — it
    must point at the trigger catalog and llms-full.txt so coding
    agents can reach them in one fetch from the well-known URL."""
    data = json.loads(_read(".well-known/agents-shipgate.json"))
    triggers_url = data.get("triggers_url", "")
    assert triggers_url.endswith("/docs/triggers.json"), (
        f".well-known/agents-shipgate.json must declare triggers_url "
        f"ending in /docs/triggers.json; got {triggers_url!r}."
    )
    llms_full_url = data.get("llms_full_url", "")
    assert llms_full_url.endswith("/llms-full.txt"), (
        f".well-known/agents-shipgate.json must declare llms_full_url "
        f"ending in /llms-full.txt; got {llms_full_url!r}."
    )


def test_llms_txt_advertises_triggers_and_llms_full():
    """llms.txt is the short fan-out for AI search; it must list the
    trigger catalog and llms-full URLs so they are discoverable from
    the canonical entry point."""
    text = _read("llms.txt")
    assert "docs/triggers.json" in text, (
        "llms.txt must reference docs/triggers.json so coding agents "
        "discover the machine-readable trigger catalog."
    )
    assert "llms-full.txt" in text, (
        "llms.txt must reference llms-full.txt so coding agents that "
        "prefer one document over chasing links can find it."
    )


def _import_build_llms_full():
    spec = importlib.util.spec_from_file_location(
        "build_llms_full", REPO_ROOT / "scripts" / "build-llms-full.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not import scripts/build-llms-full.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_llms_full_is_up_to_date():
    """llms-full.txt is generated by `scripts/build-llms-full.py`. A PR
    that touches one of the source documents must regenerate the file
    in the same commit; this test catches PRs that forget."""
    mod = _import_build_llms_full()
    expected = mod.render(REPO_ROOT)
    actual = _read("llms-full.txt")
    assert actual == expected, (
        "llms-full.txt is out of date. Re-run "
        "`python scripts/build-llms-full.py` and commit the result. "
        "Sources: " + ", ".join(mod.SOURCES)
    )


# --- Prompt mirror enforcement ------------------------------------------


_PROMPT_DIR = REPO_ROOT / "prompts"
_SKILL_PROMPT_DIR = REPO_ROOT / "skills" / "agents-shipgate" / "prompts"


_PROMPT_MIRROR_EXCLUDE = {"README.md"}


def _prompt_basenames() -> list[str]:
    return sorted(
        p.name
        for p in _PROMPT_DIR.glob("*.md")
        if p.name not in _PROMPT_MIRROR_EXCLUDE
    )


@pytest.mark.parametrize("basename", _prompt_basenames())
def test_prompt_is_mirrored_to_skill(basename):
    """Every `prompts/*.md` must have a byte-identical mirror under
    `skills/agents-shipgate/prompts/`. The skill bundle is what
    Claude Code installs and pins to a release; if a prompt drifts
    between the two locations, agents installed via the skill see
    stale guidance."""
    main = (_PROMPT_DIR / basename).read_text(encoding="utf-8")
    skill_path = _SKILL_PROMPT_DIR / basename
    assert skill_path.is_file(), (
        f"prompts/{basename} has no mirror at "
        f"skills/agents-shipgate/prompts/{basename}. Copy it over so "
        "the bundled skill stays in sync."
    )
    skill = skill_path.read_text(encoding="utf-8")
    assert main == skill, (
        f"prompts/{basename} and "
        f"skills/agents-shipgate/prompts/{basename} have diverged. "
        "Re-sync them — they must be byte-identical."
    )


def test_decide_shipgate_relevance_prompt_exists():
    """The relevance-decision prompt is the entry point for coding
    agents that haven't decided whether to run Shipgate yet — its
    presence is contractual."""
    assert (_PROMPT_DIR / "decide-shipgate-relevance.md").is_file(), (
        "prompts/decide-shipgate-relevance.md is missing. This prompt "
        "applies docs/triggers.json to a PR diff and is the gateway "
        "into the rest of the prompt library."
    )


@pytest.mark.parametrize(
    "relpath", PUBLIC_SURFACES + ("docs/ai-search-summary.md",)
)
def test_forbidden_display_names_only_in_do_not_use_lists(relpath):
    """`Agent Shipcheck` and `Agent Shipgate` (singular) are forbidden
    public/display forms. The only legitimate occurrences are inside
    explicit "do not use" / "avoid these names" lists. Catches
    accidental introduction in user-facing copy."""
    text = _read(relpath)
    if not FORBIDDEN_NAME_PATTERN.search(text):
        return
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        if not FORBIDDEN_NAME_PATTERN.search(line):
            continue
        # The "do not use" marker may sit on this line OR on the
        # previous line (lists where the heading sits on its own).
        previous = lines[line_number - 2] if line_number > 1 else ""
        context_blob = f"{previous}\n{line}"
        assert DO_NOT_USE_CONTEXT_PATTERN.search(context_blob), (
            f"{relpath}:{line_number} mentions a forbidden display "
            "form (`Agent Shipcheck` / `Agent Shipgate`) without a "
            "'do not use' / 'avoid these names' / 'forbidden' marker "
            "on the same or previous line. Use the canonical "
            "`Agents Shipgate` instead.\n  line: "
            f"{line.strip()!r}"
        )


# ---------------------------------------------------------------------------
# Pre-commit hooks regex vs. docs/triggers.json parity
# ---------------------------------------------------------------------------
#
# The root .pre-commit-hooks.yaml exposes a `files:` regex that covers a
# subset of docs/triggers.json — specifically the path-based positive
# triggers (the regex can't match diff-only triggers like
# TRIGGER-FUNCTION-TOOL-DECORATOR). When the catalog adds a path-based
# trigger, the hook regex must add a matching pattern; otherwise the docs
# claim parity that doesn't hold.

# Path-based positive triggers in docs/triggers.json. Each entry maps the
# trigger ID to a representative path that should match the hook regex.
# Excludes diff-only triggers (decorator, version bump) and
# file_present-only triggers (existing manifest), neither of which the
# hook regex can cover.
_HOOK_PATH_TRIGGER_FIXTURES = {
    "TRIGGER-MCP-EXPORT-CHANGED": [
        "server/mcp-export.json",
        ".agents-shipgate/cached-mcp.json",
    ],
    "TRIGGER-OPENAPI-SPEC-CHANGED": [
        "api/openapi.yaml",
        "api/swagger.json",
    ],
    "TRIGGER-STATIC-TOOL-INVENTORY-CHANGED": [
        "tools/openai-tools.json",
        "tools/anthropic-tools.json",
    ],
    "TRIGGER-PROMPTS-OR-POLICIES": [
        "prompts/system.md",
        "policies/refund.md",
    ],
    "TRIGGER-SHIPGATE-MANIFEST": [
        "shipgate.yaml",
    ],
    "TRIGGER-SHIPGATE-CI-WORKFLOW": [
        ".github/workflows/agents-shipgate.yml",
        ".github/workflows/agents-shipgate.yaml",
    ],
}


def _hook_files_regex() -> re.Pattern[str]:
    """Extract the canonical `agents-shipgate` hook's `files:` regex
    from the root .pre-commit-hooks.yaml so the test parses the same
    pattern pre-commit will at install time."""
    import yaml

    text = _read(".pre-commit-hooks.yaml")
    hooks = yaml.safe_load(text)
    advisory = next(h for h in hooks if h["id"] == "agents-shipgate")
    pattern = advisory["files"]
    # pre-commit compiles with re.VERBOSE since the manifest uses `|`
    # block scalars with comments and whitespace.
    return re.compile(pattern, re.VERBOSE)


def test_pre_commit_hook_regex_covers_every_path_based_trigger():
    """The hook docs (README, integrations.md, hook file header) claim
    the `files:` regex covers every path-based trigger in
    docs/triggers.json. Pin that claim: each representative path for
    each path-based trigger ID MUST match the regex. If this fails, a
    new path-based trigger landed in the catalog without a
    corresponding regex update."""
    pattern = _hook_files_regex()
    triggers = _load_triggers_json()
    catalog_ids = {rule["id"] for rule in triggers["rules"]}

    # Sanity: every fixture id must exist in the catalog. Catches a
    # silent rename in triggers.json.
    for trigger_id in _HOOK_PATH_TRIGGER_FIXTURES:
        assert trigger_id in catalog_ids, (
            f"Fixture references {trigger_id!r} but docs/triggers.json "
            "doesn't define it. Either the trigger was renamed, or the "
            "fixture is stale."
        )

    for trigger_id, sample_paths in _HOOK_PATH_TRIGGER_FIXTURES.items():
        for path in sample_paths:
            assert pattern.match(path), (
                f"hook `files:` regex does NOT match {path!r} "
                f"(covers {trigger_id}). Either add a clause to "
                ".pre-commit-hooks.yaml or narrow the doc claim that "
                "the regex mirrors docs/triggers.json."
            )


def test_pre_commit_hook_regex_skips_docs_only_paths():
    """Negative control: the hook must NOT fire on pure docs / tests /
    config files that aren't tool-surface artifacts. Mirrors the
    `TRIGGER-DOCS-ONLY-NEGATIVE` rule."""
    pattern = _hook_files_regex()
    docs_only_paths = [
        "README.md",
        "docs/index.md",
        "tests/test_foo.py",
        "src/agents_shipgate/cli/main.py",
        ".github/workflows/release.yml",  # non-shipgate workflow
    ]
    for path in docs_only_paths:
        assert not pattern.match(path), (
            f"hook `files:` regex MATCHES {path!r}; that path is not a "
            "tool-surface artifact and the hook should not fire on it. "
            "Tighten the regex."
        )


def test_pre_commit_local_docs_show_same_path_trigger_clauses():
    """The copy-paste `repo: local` snippet must not lag the root hook.

    Downstream users often copy the local snippet directly instead of using
    the canonical `repo: https://...` install form, so the documented regex
    needs the same path-based trigger clauses as `.pre-commit-hooks.yaml`.
    """
    text = _read("docs/integrations.md")
    for clause in (
        r".*swagger.*\.(yaml|yml|json)",
        r"\.agents-shipgate/.*\.json",
        r"\.github/workflows/agents-shipgate\.(yaml|yml)",
    ):
        assert clause in text, (
            "docs/integrations.md local pre-commit snippet is missing "
            f"{clause!r}; keep it aligned with the root hook regex."
        )


def test_pre_commit_docs_do_not_reference_missing_trigger_subcommand():
    """`triggers` is a module entry point, not a top-level Typer command."""
    text = _read(".pre-commit-hooks.yaml")
    assert "agents-shipgate triggers --diff" not in text
    assert "python -m agents_shipgate.triggers --git-diff HEAD" in text
