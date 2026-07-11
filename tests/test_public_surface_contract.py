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
from typing import get_args

import pytest

from agents_shipgate import __version__
from agents_shipgate.packet.disclaimer import PACKET_NON_PROOF_HEADLINE
from agents_shipgate.report.markdown import DISCLAIMER
from agents_shipgate.schemas.attestation import ATTESTATION_SCHEMA_VERSION
from agents_shipgate.schemas.capabilities import (
    CAPABILITY_LOCK_DIFF_SCHEMA_VERSION,
    CAPABILITY_LOCK_SCHEMA_VERSION,
    CAPABILITY_STANDARD_VERSION,
)
from agents_shipgate.schemas.contract import (
    CONTRACT_VERSION,
    GATING_SIGNAL,
    MANUAL_REVIEW_SIGNALS,
    SUPPORTED_INPUTS,
    build_contract_payload,
)
from agents_shipgate.schemas.diagnostics import NextActionKind
from agents_shipgate.schemas.disclaimers import STATIC_VERDICT_DISCLAIMER
from agents_shipgate.schemas.governance_benchmark import (
    GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION,
    GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION,
)
from agents_shipgate.schemas.host_grants import HOST_GRANTS_INVENTORY_SCHEMA_VERSION
from agents_shipgate.schemas.org_evidence_bundle import ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION
from agents_shipgate.schemas.packet import EvidencePacket
from agents_shipgate.schemas.registry import REGISTRY_SCHEMA_VERSION
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.triggers import evaluate, load_triggers

REPO_ROOT = Path(__file__).resolve().parent.parent

CURRENT_REPORT_SCHEMA_VERSION = str(ReadinessReport.model_fields["report_schema_version"].default)
CURRENT_REPORT_SCHEMA = f"report-schema.v{CURRENT_REPORT_SCHEMA_VERSION}.json"
CURRENT_PACKET_SCHEMA_VERSION = str(EvidencePacket.model_fields["packet_schema_version"].default)
CURRENT_PACKET_SCHEMA = f"packet-schema.v{CURRENT_PACKET_SCHEMA_VERSION}.json"
# The source tree is on the 0.16 beta line while install snippets and Action
# examples must continue to name the latest tag that actually exists.
LATEST_PUBLISHED_VERSION = "0.15.0"
# Frozen report schemas that still appear in public surfaces must be labeled as
# frozen/legacy/older instead of being mistaken for the current schema.
LEGACY_REPORT_SCHEMA_PATTERN = re.compile(
    r"report-schema\.v0\.(?:7|8|9|10|11|12|13|14|15|16|17|18|19|20|21|22|23|24|25|26|27|28|29|30)\.json"
)
ANY_REPORT_SCHEMA_PATTERN = re.compile(r"report-schema\.v0\.\d+\.json")
ANY_PACKET_SCHEMA_PATTERN = re.compile(r"packet-schema\.v\d+\.\d+\.json")
LEGACY_PACKET_SCHEMA_PATTERN = re.compile(r"packet-schema\.v0\.(?:1|2|3|4|5|6|7)\.json")
PACKET_ANCHOR_PATTERN = re.compile(r"#release-evidence-packet-v(\d+)")
SUMMARY_STATUS_PATTERN = re.compile(r"summary\.status\b|summary\.\{[^}]*status[^}]*\}")
LEGACY_CONTEXT_WORDS = re.compile(
    r"\b(?:frozen|legacy|compat|compatibility|baseline-blind|preserved|"
    r"older|pre-v|kept for|v0\.7 caller|previously)\b",
    re.IGNORECASE,
)
CURRENT_CONTEXT_WORDS = re.compile(r"\bcurrent\b", re.IGNORECASE)
CONTEXT_WINDOW = 400  # ~one paragraph; tight enough that the original
# stale `.claude/commands/shipgate.md` (no legacy
# marker for hundreds of chars) would still fail.

VERSION_RE = r"\d+\.\d+\.\d+(?:[A-Za-z]+\d*)?"
ACTION_PIN_PATTERN = re.compile(rf"ThreeMoonsLab/agents-shipgate@v({VERSION_RE})")
PIP_PIN_PATTERN = re.compile(rf"agents-shipgate==({VERSION_RE})")
# Zero-install runner pin recommended by the agent-facing install
# snippets: ``uvx agents-shipgate@X.Y.Z``. The ``@v`` GitHub Action form
# is NOT matched (a digit must follow ``@``), nor is the ``==`` pip form,
# so this guards the uvx literal specifically.
UVX_PIN_PATTERN = re.compile(rf"agents-shipgate@({VERSION_RE})")
SHIPGATE_VERSION_INPUT_PATTERN = re.compile(rf"shipgate_version:\s*['\"]({VERSION_RE})['\"]")
# Surfaces that name the *latest released* version inline (not as an
# Action / pip / shipgate_version pin) and must move with the package
# version when a public tag is cut. Each entry is a (path, regex) pair where
# the regex's first capture group is compared with LATEST_PUBLISHED_VERSION.
# The regexes are anchored to surrounding phrasing so
# historical version mentions in the same file (e.g. ROADMAP.md's
# release-history list, faq.md's older v0.x narrative) are not matched.
VERSION_LITERAL_TARGETS = (
    (
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        re.compile(rf"placeholder:\s*\"v({VERSION_RE})\""),
    ),
    (
        "docs/distribution.md",
        re.compile(rf"Pinned GitHub Action release tags[^\n]*?including\s+`v({VERSION_RE})`"),
    ),
    (
        "docs/faq.md",
        re.compile(rf"v({VERSION_RE}) is the latest published pre-1\.0 beta"),
    ),
    (
        "ROADMAP.md",
        re.compile(rf"Latest release:\s*`v({VERSION_RE})`"),
    ),
)
# Forbidden public/display forms. Case-sensitive on purpose: `Agents
# Shipgate` (canonical) must never match. The four banned variants
# below mirror the "Do not use" list in AGENTS.md §Naming (canonical).
FORBIDDEN_NAME_PATTERN = re.compile(
    r"(?<![A-Za-z])("
    r"Agent\s+(?:Shipcheck|Shipgate)"  # singular display: "Agent Shipgate"
    r"|Agents-Shipgate"  # display kebab
    r"|agents\s+shipgate"  # display lowercase
    r")(?![A-Za-z])"
)
# `agent_shipgate` (singular underscore) is always wrong; the correct
# Python module is `agents_shipgate` (plural). Pinned separately
# because Python contexts otherwise need `agents_shipgate` to pass.
SINGULAR_UNDERSCORE_PATTERN = re.compile(r"(?<![A-Za-z_])agent_shipgate(?![A-Za-z_])")
DO_NOT_USE_CONTEXT_PATTERN = re.compile(
    r"do\s*\*{0,2}\s*not\s*\*{0,2}\s*use|avoid these names|forbidden",
    re.IGNORECASE,
)
POSITIONING_PHRASE = "The deterministic merge gate for AI-generated agent capability changes"
POSITIONING_SCAN_DOCSTRING = (
    "the deterministic merge gate for AI-generated agent capability changes"
)
POSITIONING_SURFACES = (
    "README.md",
    "AGENTS.md",
    "pyproject.toml",
    "action.yml",
    ".well-known/agents-shipgate.json",
    "llms.txt",
    "docs/overview.md",
    "docs/category.md",
    "docs/faq.md",
    "docs/concepts.md",
    "docs/glossary.md",
    "docs/ai-search-summary.md",
    "docs/target-repo-agent-snippets.md",
    "benchmark/setup-variants/10-agents-md/AGENTS.md.template",
    "skills/agents-shipgate/SKILL.md",
    ".claude/commands/shipgate.md",
    ".cursor/rules/agents-shipgate.mdc",
    "src/agents_shipgate/cli/main.py",
    "src/agents_shipgate/cli/discovery/agent_instructions/renderers/agents_md.py",
    "src/agents_shipgate/cli/discovery/agent_instructions/renderers/claude_md.py",
    "src/agents_shipgate/cli/discovery/agent_instructions/renderers/cursor.py",
    "src/agents_shipgate/cli/discovery/agent_instructions/renderers/pr_template.py",
)
PRIMARY_POSITIONING_SURFACES = (
    *POSITIONING_SURFACES,
    "src/agents_shipgate/cli/_register_scan.py",
    "src/agents_shipgate/report/markdown.py",
    "src/agents_shipgate/packet/disclaimer.py",
)
BROAD_POSITIONING_PATTERN = re.compile(
    r"healthcare[\s\-]+for[\s\-]+agents|"
    r"agent[\s\-]+lifecycle[\s\-]+readiness|"
    r"governance[\s\-]+platform|"
    r"enterprise[\s\-]+governance|"
    r"across[\s\-]+the[\s\-]+agent[\s\-]+lifecycle",
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
    "docs/architecture.md",
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
    "examples/github-actions/07-block-on-blocked-verdict.yml",
    "examples/github-actions/08-require-mergeable.yml",
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
    "prompts/decide-shipgate-relevance.md",
    "skills/agents-shipgate/prompts/decide-shipgate-relevance.md",
    "prompts/stabilize-strict-mode.md",
    "skills/agents-shipgate/prompts/stabilize-strict-mode.md",
    "skills/agents-shipgate/ci-recipes/advisory-pr-comment.yml",
    ".agents/skills/agents-shipgate/assets/advisory-pr-comment.yml",
)


def _load_pyproject_version() -> str:
    """Read `[project].version` from pyproject.toml — single source of
    truth for the package version that every public surface must echo."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


def _has_legacy_context(text: str, start: int, end: int) -> bool:
    snippet = text[max(0, start - CONTEXT_WINDOW) : end + CONTEXT_WINDOW]
    return bool(LEGACY_CONTEXT_WORDS.search(snippet))


def _has_current_context(text: str, start: int, end: int) -> bool:
    snippet = text[max(0, start - CONTEXT_WINDOW) : end + CONTEXT_WINDOW]
    return bool(CURRENT_CONTEXT_WORDS.search(snippet))


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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
        f"ThreeMoonsLab/agents-shipgate@v{LATEST_PUBLISHED_VERSION}"
    )
    assert data.get("release_status", {}).get("latest_release") == (
        f"v{LATEST_PUBLISHED_VERSION}"
    )
    outputs = data.get("outputs", [])
    for expected in (
        "packet_md",
        "packet_json",
        "packet_html",
        "capability_lock_diff_md",
        "feedback_json",
        "attestation_json",
        "org_evidence_bundle_json",
        "host_grants_json",
        "org_status_json",
    ):
        assert expected in outputs, (
            f".well-known/agents-shipgate.json outputs missing {expected!r}; "
            "the Release Evidence Packet and feedback export are first-class outputs."
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
    assert data.get("contract_version") == CONTRACT_VERSION
    assert data.get("static_analysis_only") is True
    assert data.get("runtime_behavior_verified") is False
    assert data.get("static_verdict_disclaimer") == STATIC_VERDICT_DISCLAIMER
    assert data.get("passed_verdict_contract", "").endswith(
        "/docs/passed-verdict-contract.md"
    )
    assert data.get("report_schema_version") == contract["report_schema_version"]
    assert data.get("packet_schema_version") == contract["packet_schema_version"]
    assert data.get("agent_result_schema_version") == contract["agent_result_schema_version"]
    assert data.get("agent_result_schema_path") == contract["agent_result_schema_path"]
    assert data.get("agent_result_control_fields") == contract["agent_result_control_fields"]
    assert data.get("verifier_schema_version") == contract["verifier_schema_version"]
    assert data.get("verify_run_schema_version") == contract["verify_run_schema_version"]
    assert data.get("agent_handoff_schema_version") == contract["agent_handoff_schema_version"]
    assert data.get("agent_handoff_schema_path") == contract["agent_handoff_schema_path"]
    assert data.get("agent_handoff_artifact") == contract["agent_handoff_artifact"]
    assert data.get("codex_boundary_result_schema_version") == (
        contract["codex_boundary_result_schema_version"]
    )
    assert data.get("capability_standard_version") == CAPABILITY_STANDARD_VERSION
    assert data.get("capability_lock_schema_version") == (
        contract["capability_lock_schema_version"]
    )
    assert data.get("capability_lock_diff_schema_version") == (
        contract["capability_lock_diff_schema_version"]
    )
    assert data.get("agent_read_order") == contract["agent_read_order"]
    assert data.get("verifier_read_order") == contract["verifier_read_order"]
    assert data.get("do_not_auto_assert") == contract["do_not_auto_assert"]
    assert "action_effect" in contract["do_not_auto_assert"]
    assert "action_authority" in contract["do_not_auto_assert"]
    assert data.get("agent_interface_operations") == contract["agent_interface_operations"]
    assert data.get("exit_code_policy") == contract["exit_code_policy"]
    assert data.get("mcp_tools") == contract["mcp_tools"]
    commands = data.get("commands", {})
    assert commands.get("agent_check_codex") == contract["commands"]["agent_check_codex"]
    assert commands.get("agent_check_claude_code") == (
        contract["commands"]["agent_check_claude_code"]
    )
    assert commands.get("agent_check_cursor") == contract["commands"]["agent_check_cursor"]
    artifacts = data.get("artifacts", {})
    assert artifacts.get("local_contract") == (".shipgate/agent-contract.json")
    assert artifacts.get("verify_run") == contract["artifacts"]["verify_run"]
    assert artifacts.get("agent_handoff") == contract["artifacts"]["agent_handoff"]
    report_url = schemas.get("report", "")
    assert CURRENT_REPORT_SCHEMA in report_url, (
        f".well-known schemas.report must point to {CURRENT_REPORT_SCHEMA}; got {report_url!r}."
    )
    packet_url = schemas.get("packet", "")
    assert CURRENT_PACKET_SCHEMA in packet_url, (
        f".well-known schemas.packet must point to {CURRENT_PACKET_SCHEMA}; got {packet_url!r}."
    )
    feedback_url = schemas.get("feedback", "")
    assert "feedback-schema.v0.1.json" in feedback_url, (
        ".well-known schemas.feedback must point to docs/feedback-schema.v0.1.json; "
        f"got {feedback_url!r}."
    )
    capability_lock_url = schemas.get("capability_lock", "")
    assert f"capability-lock-schema.v{CAPABILITY_LOCK_SCHEMA_VERSION}.json" in (
        capability_lock_url
    ), (
        ".well-known schemas.capability_lock must point to the current "
        f"capability lock schema; got {capability_lock_url!r}."
    )
    capability_diff_url = schemas.get("capability_lock_diff", "")
    assert (
        f"capability-lock-diff-schema.v{CAPABILITY_LOCK_DIFF_SCHEMA_VERSION}.json"
        in capability_diff_url
    ), (
        ".well-known schemas.capability_lock_diff must point to the current "
        f"capability lock diff schema; got {capability_diff_url!r}."
    )
    attestation_url = schemas.get("attestation", "")
    assert f"attestation-schema.v{ATTESTATION_SCHEMA_VERSION}.json" in (attestation_url), (
        ".well-known schemas.attestation must point to the current "
        f"attestation schema; got {attestation_url!r}."
    )
    assert data.get("attestation_schema_version") == contract["attestation_schema_version"]
    assert data.get("registry_schema_version") == contract["registry_schema_version"]
    assert data.get("org_evidence_bundle_schema_version") == (
        contract["org_evidence_bundle_schema_version"]
    )
    assert data.get("host_grants_inventory_schema_version") == (
        contract["host_grants_inventory_schema_version"]
    )
    registry_url = schemas.get("registry", "")
    assert f"registry-schema.v{REGISTRY_SCHEMA_VERSION}.json" in registry_url, (
        ".well-known schemas.registry must point to the current "
        f"registry schema; got {registry_url!r}."
    )
    bundle_url = schemas.get("org_evidence_bundle", "")
    assert "org-evidence-bundle-schema.v1.json" in bundle_url
    assert data.get("org_evidence_bundle_schema_version") == (
        ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION
    )
    host_grants_url = schemas.get("host_grants_inventory", "")
    assert (
        f"host-grants-inventory-schema.v{HOST_GRANTS_INVENTORY_SCHEMA_VERSION}.json"
        in host_grants_url
    ), (
        ".well-known schemas.host_grants_inventory must point to the current "
        f"host grants schema; got {host_grants_url!r}."
    )
    benchmark_catalog_url = schemas.get("governance_benchmark_catalog", "")
    assert (
        "governance-benchmark-catalog-schema."
        f"v{GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION}.json" in benchmark_catalog_url
    ), (
        ".well-known schemas.governance_benchmark_catalog must point to the "
        f"current catalog schema; got {benchmark_catalog_url!r}."
    )
    benchmark_result_url = schemas.get("governance_benchmark_result", "")
    assert (
        "governance-benchmark-result-schema."
        f"v{GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION}.json" in benchmark_result_url
    ), (
        ".well-known schemas.governance_benchmark_result must point to the "
        f"current result schema; got {benchmark_result_url!r}."
    )
    assert "verify_run" in schemas and "verify-run-schema.v1.json" in schemas["verify_run"]
    assert (
        "agent_handoff" in schemas
        and "agent-handoff-schema.v2.json" in schemas["agent_handoff"]
    )
    assert (
        "codex_boundary_result" in schemas
        and "codex-boundary-result-schema.v1.json" in schemas["codex_boundary_result"]
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
    assert f"Latest release: `v{LATEST_PUBLISHED_VERSION}`" in text, (
        "docs/agent-contract-current.md must name the latest published tag."
    )
    assert f"In-tree runtime: `{contract['cli_version']}`" in text, (
        "docs/agent-contract-current.md must agree with the runtime contract's cli_version."
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
    assert "commands" in text and "verifier_read_order" in text, (
        "docs/agent-contract-current.md must mention the runtime contract's "
        "agent-operational command/read-order fields."
    )
    assert "agent-handoff.json" in text and "agent_handoff_schema_version" in text, (
        "docs/agent-contract-current.md must document the v6 agent handoff artifact."
    )
    assert "findings[].provenance_kind" in MANUAL_REVIEW_SIGNALS
    assert "agents-shipgate findings" in text, (
        "docs/agent-contract-current.md must make provenance_kind operational "
        "via the findings filter command."
    )
    assert "never changes the release decision" in text, (
        "docs/agent-contract-current.md must state that provenance_kind is "
        "reviewer triage only, not a gate input."
    )
    assert CURRENT_PACKET_SCHEMA in text, (
        "docs/agent-contract-current.md must reference the current packet "
        f"schema (v{CURRENT_PACKET_SCHEMA_VERSION}) so coding agents know "
        "about the Release Evidence Packet."
    )


def test_architecture_doc_contract_stamp_matches_runtime():
    """docs/architecture.md is easy to stale-date during schema bumps.
    Pin its stamp to the runtime contract so CI catches future drift."""
    text = _read("docs/architecture.md")
    stamp = re.search(
        r"Current as of\s+(?P<date>\d{4}-\d{2}-\d{2});\s+"
        r"auto-checked against `agents-shipgate contract --json`:\s*"
        r"runtime contract `(?P<contract>[^`]+)`, "
        r"report schema `v(?P<report>\d+\.\d+)`, "
        r"packet schema `v(?P<packet>\d+\.\d+)`\.",
        text,
    )
    assert stamp, (
        "docs/architecture.md must carry a contract stamp in the form "
        "'Current as of YYYY-MM-DD; auto-checked against "
        "`agents-shipgate contract --json`: runtime contract `N`, "
        "report schema `vX.Y`, packet schema `vX.Y`.'"
    )
    assert stamp.group("date") == "2026-07-09", (
        "docs/architecture.md contract-check date must stay pinned to "
        "2026-07-09 until a deliberate architecture-doc refresh moves it."
    )
    assert stamp.group("contract") == CONTRACT_VERSION, (
        f"docs/architecture.md says runtime contract "
        f"{stamp.group('contract')!r}; runtime is {CONTRACT_VERSION!r}."
    )
    assert stamp.group("report") == CURRENT_REPORT_SCHEMA_VERSION, (
        f"docs/architecture.md says report schema "
        f"{stamp.group('report')!r}; runtime is "
        f"{CURRENT_REPORT_SCHEMA_VERSION!r}."
    )
    assert stamp.group("packet") == CURRENT_PACKET_SCHEMA_VERSION, (
        f"docs/architecture.md says packet schema "
        f"{stamp.group('packet')!r}; runtime is "
        f"{CURRENT_PACKET_SCHEMA_VERSION!r}."
    )
    assert "reviewer_summary" in text, (
        "docs/architecture.md must mention the v0.20 reviewer_summary "
        "surface so architecture readers see the current reviewer lens "
        "projection."
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
    report_match = re.search(r"Current report schema:\s*`(\d+\.\d+)`", text)
    packet_match = re.search(r"Current packet schema:\s*`(\d+\.\d+)`", text)
    release_match = re.search(rf"Latest release:\s*`v({VERSION_RE})`", text)
    runtime_match = re.search(rf"In-tree runtime:\s*`({VERSION_RE})`", text)
    assert report_match, (
        "docs/agent-contract-current.md must declare 'Current report "
        "schema: `X.Y`' so the test constants can be cross-checked."
    )
    assert packet_match, (
        "docs/agent-contract-current.md must declare 'Current packet schema: `X.Y`'."
    )
    assert release_match, "docs/agent-contract-current.md must declare 'Latest release: `vX.Y.Z`'."
    assert runtime_match, "docs/agent-contract-current.md must declare 'In-tree runtime: `X.Y.Z`'."
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
    assert release_match.group(1) == LATEST_PUBLISHED_VERSION, (
        f"contract doc says latest release is "
        f"v{release_match.group(1)}; expected the latest published tag "
        f"v{LATEST_PUBLISHED_VERSION}."
    )
    assert runtime_match.group(1) == _load_pyproject_version(), (
        f"contract doc says in-tree runtime is {runtime_match.group(1)}; "
        f"pyproject.toml says {_load_pyproject_version()}."
    )


def test_runtime_and_published_versions_propagate_to_metadata_surfaces():
    """Keep the in-tree runtime and latest published tag distinct.

    Pre-release source metadata follows pyproject.toml; install snippets and
    release discovery continue to name the latest tag that actually exists.
    """
    expected = _load_pyproject_version()

    # src/agents_shipgate/__init__.__version__
    import agents_shipgate

    assert agents_shipgate.__version__ == expected, (
        f"agents_shipgate.__version__ is "
        f"{agents_shipgate.__version__!r}; pyproject.toml says "
        f"{expected!r}. Update src/agents_shipgate/__init__.py."
    )

    # .well-known distinguishes the source-tree runtime from installable tags.
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
    assert action_match.group(1) == LATEST_PUBLISHED_VERSION, (
        f".well-known package.github_action pins "
        f"v{action_match.group(1)}; latest published is v{LATEST_PUBLISHED_VERSION}."
    )
    assert well_known["release_status"]["latest_release"] == (
        f"v{LATEST_PUBLISHED_VERSION}"
    )

    # llms.txt install/release guidance must name the published tag.
    llms_text = _read("llms.txt")
    llms_release = re.search(rf"Latest public release:\s*v({VERSION_RE})", llms_text)
    assert llms_release, "llms.txt must declare 'Latest public release: vX.Y.Z'."
    assert llms_release.group(1) == LATEST_PUBLISHED_VERSION, (
        f"llms.txt 'Latest public release' is "
        f"v{llms_release.group(1)}; latest published is v{LATEST_PUBLISHED_VERSION}."
    )
    llms_action = ACTION_PIN_PATTERN.search(llms_text)
    assert llms_action, (
        "llms.txt must include a ThreeMoonsLab/agents-shipgate@vX.Y.Z "
        "Action pin so coding agents know the canonical version."
    )
    assert llms_action.group(1) == LATEST_PUBLISHED_VERSION, (
        f"llms.txt Action pin is v{llms_action.group(1)}; latest published is "
        f"v{LATEST_PUBLISHED_VERSION}."
    )

    # docs/agent-contract-current.md
    contract_text = _read("docs/agent-contract-current.md")
    contract_release = re.search(rf"Latest release:\s*`v({VERSION_RE})`", contract_text)
    assert contract_release and contract_release.group(1) == LATEST_PUBLISHED_VERSION, (
        "docs/agent-contract-current.md 'Latest release' must name the latest "
        f"published tag `v{LATEST_PUBLISHED_VERSION}`."
    )
    assert f"In-tree runtime: `{expected}`" in contract_text


def test_release_tag_consistency_checks_published_tag_not_prerelease_runtime():
    """Main may carry a beta runtime before its release tag exists.

    The origin-tag check must validate the explicit latest-published field,
    otherwise every pre-release version bump makes main red by construction.
    """

    workflow = _read(".github/workflows/ci.yml")
    assert '.well-known/agents-shipgate.json' in workflow
    assert '["release_status"]["latest_release"]' in workflow
    assert 'refs/tags/${latest_release}' in workflow
    assert 'refs/tags/v${version}' not in workflow


def _file_lines_with_pin(path: str, pattern: re.Pattern[str]):
    """Yield (line_number, line_text, captured_version) for every
    pin-pattern hit in the file. Empty if the file has no pins."""
    text = _read(path)
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            yield line_number, line, match.group(1)


@pytest.mark.parametrize("relpath", ACTION_PIN_FILES)
def test_action_pins_match_latest_published_version(relpath):
    """Every `ThreeMoonsLab/agents-shipgate@vX.Y.Z` pin in a public
    surface must equal the latest published tag. In-tree pre-release versions
    must not leak into install snippets before their tag exists."""
    expected = LATEST_PUBLISHED_VERSION
    for line_number, line, found in _file_lines_with_pin(relpath, ACTION_PIN_PATTERN):
        assert found == expected, (
            f"{relpath}:{line_number} pins "
            f"ThreeMoonsLab/agents-shipgate@v{found}; latest published "
            f"is v{expected}. Update only after that tag exists.\n  "
            f"line: {line.strip()!r}"
        )


@pytest.mark.parametrize("relpath", ACTION_PIN_FILES)
def test_pip_pins_match_latest_published_version(relpath):
    """Every `agents-shipgate==X.Y.Z` install pin in a public surface
    must equal the latest published version. Same drift guard as the Action
    pin test, for pip-based CI examples."""
    expected = LATEST_PUBLISHED_VERSION
    for line_number, line, found in _file_lines_with_pin(relpath, PIP_PIN_PATTERN):
        assert found == expected, (
            f"{relpath}:{line_number} pins agents-shipgate=={found}; "
            f"latest published is {expected}. Update the pin only after "
            f"that release exists.\n  line: "
            f"{line.strip()!r}"
        )


@pytest.mark.parametrize("relpath", ACTION_PIN_FILES)
def test_uvx_pins_match_latest_published_version(relpath):
    """Every ``uvx agents-shipgate@X.Y.Z`` zero-install pin in a public
    surface must equal the package version. The agent-facing install
    snippets recommend this pinned runner so a coding agent never shells
    out to a stale ``PATH`` build; without this guard a pyproject bump
    could leave a stale ``uvx agents-shipgate@…`` literal in a bundled
    prompt. The ``pipx run agents-shipgate==X.Y.Z`` form is already
    covered by ``PIP_PIN_PATTERN`` and the ``@v`` Action form by
    ``ACTION_PIN_PATTERN``."""
    expected = LATEST_PUBLISHED_VERSION
    for line_number, line, found in _file_lines_with_pin(relpath, UVX_PIN_PATTERN):
        assert found == expected, (
            f"{relpath}:{line_number} pins uvx agents-shipgate@{found}; "
            f"latest published is {expected}. Update the pin only after "
            f"that release exists.\n  line: "
            f"{line.strip()!r}"
        )


@pytest.mark.parametrize("relpath", ACTION_PIN_FILES)
def test_shipgate_version_inputs_match_latest_published_version(relpath):
    """The `shipgate_version: '<version>'` Action input in workflow
    examples must match the package version too. Catches a stale
    matrix where the Action pin is updated but the CLI install
    version inside it is left behind."""
    expected = LATEST_PUBLISHED_VERSION
    for line_number, line, found in _file_lines_with_pin(relpath, SHIPGATE_VERSION_INPUT_PATTERN):
        assert found == expected, (
            f"{relpath}:{line_number} sets shipgate_version: "
            f"'{found}'; latest published is {expected}.\n  line: "
            f"{line.strip()!r}"
        )


@pytest.mark.parametrize("relpath,pattern", VERSION_LITERAL_TARGETS)
def test_version_literals_match_latest_published_version(relpath, pattern):
    """Plain release-version literals on these public surfaces (the
    bug-report placeholder, distribution.md's release-tag list,
    faq.md's 'latest released version' line, ROADMAP.md's latest-release
    line) must move with the latest published tag. The
    Action / pip / shipgate_version pin tests don't catch these
    because the literals aren't pins."""
    expected = LATEST_PUBLISHED_VERSION
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
        f"public copy; latest published is v{expected}. Bump the "
        "literal only after that tag exists.\n  match: "
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


def _load_errors_json() -> dict:
    return json.loads(_read("docs/errors.json"))


def _emitted_error_kinds_in_source() -> set[str]:
    """Walk `src/agents_shipgate/cli/` and return every literal string
    passed as the first argument to `emit_agent_mode_error(...)` or to
    `_emit_input_error(...)` (the apply-patches helper that uses the
    same one-line stderr format). Source-of-truth for which kinds the
    runtime actually emits."""
    src_dir = REPO_ROOT / "src" / "agents_shipgate" / "cli"
    pattern = re.compile(
        r"(?:emit_agent_mode_error|_emit_input_error)\(\s*\n?\s*\"([a-z_]+)\"",
        re.MULTILINE,
    )
    kinds: set[str] = set()
    for path in src_dir.rglob("*.py"):
        kinds.update(pattern.findall(path.read_text(encoding="utf-8")))
    return kinds


def test_errors_json_lists_every_runtime_emitted_kind():
    """`docs/errors.json` is the published catalog of error kinds an
    agent might see when `AGENTS_SHIPGATE_AGENT_MODE=1`. Every kind
    actually emitted by the runtime must appear in the catalog —
    otherwise downstream agents pre-fetching the catalog will pattern-
    match against a stale list."""
    catalog = _load_errors_json()
    catalog_ids = {entry["id"] for entry in catalog["errors"]}
    emitted = _emitted_error_kinds_in_source()

    missing_from_catalog = emitted - catalog_ids
    assert not missing_from_catalog, (
        "docs/errors.json missing kinds the runtime actually emits: "
        f"{sorted(missing_from_catalog)}. Add them to the catalog."
    )

    missing_from_runtime = catalog_ids - emitted
    assert not missing_from_runtime, (
        "docs/errors.json lists kinds the runtime never emits: "
        f"{sorted(missing_from_runtime)}. Either delete them from the "
        "catalog or add the emit site that justifies them."
    )


def test_errors_json_lists_every_kind_documented_in_agents_md():
    """AGENTS.md enumerates the error kinds in prose; the catalog and
    the prose must agree. Catches the drift mode where someone adds a
    kind to the catalog (or to AGENTS.md) without updating the other."""
    catalog = _load_errors_json()
    catalog_ids = {entry["id"] for entry in catalog["errors"]}
    agents_md = _read("AGENTS.md")
    for error_id in catalog_ids:
        assert f"`{error_id}`" in agents_md, (
            f"AGENTS.md does not mention error kind `{error_id}` "
            "(must be in the agent-mode error-kind list)."
        )


def test_errors_json_schema_version_is_pinned():
    """Pin the catalog's schema version explicitly so a bump is a
    deliberate breaking-change moment. v0.1 is the first published
    version — bumping requires updating the public-surface mentions
    and the contract doc in the same PR."""
    catalog = _load_errors_json()
    assert catalog["schema_version"] == "0.1", (
        f"docs/errors.json schema_version moved off 0.1 — got "
        f"{catalog['schema_version']!r}. Bump deliberately and "
        "update AGENTS.md / docs/agent-contract-current.md to "
        "match in the same PR."
    )
    assert catalog["agent_mode_env_var"] == "AGENTS_SHIPGATE_AGENT_MODE"
    for entry in catalog["errors"]:
        assert isinstance(entry.get("exit_code"), int), (
            f"errors.json entry {entry['id']!r} missing integer exit_code."
        )
        assert entry.get("description"), f"errors.json entry {entry['id']!r} missing description."


def test_errors_json_next_action_kinds_match_diagnostic_contract():
    catalog = _load_errors_json()
    assert set(catalog["next_action_kinds"]) == set(get_args(NextActionKind))


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


def _agents_md_trigger_table_rows() -> list[tuple[str, str]]:
    """Parse the (trigger, decision) data rows of the AGENTS.md
    "Should I run Shipgate on this PR?" markdown table."""
    agents_md = _read("AGENTS.md")
    start = agents_md.index("| Trigger in this PR")
    table_lines: list[str] = []
    for line in agents_md[start:].splitlines():
        if not line.startswith("|"):
            break
        table_lines.append(line)
    rows: list[tuple[str, str]] = []
    for line in table_lines[2:]:  # skip header + separator
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2:
            rows.append((cells[0], cells[1]))
    return rows


def test_agents_md_trigger_table_rows_are_covered_by_catalog():
    """Reverse of the verbatim-rows test: every row in the AGENTS.md
    trigger table must be backed by a docs/triggers.json rule. A 'Yes'
    row needs a run_shipgate/force_run rule with that exact
    ``agents_md_row``; a plain 'Skip' row needs a skip_shipgate rule;
    a 'dry-run' row is the advisory refactor case (the dry_run rule uses
    different prose, so only require that a dry_run rule exists).

    Catches the drift where a new tool surface is added to the prose
    table (e.g. n8n) but no catalog rule is added, so a coding agent
    applying triggers.json would silently miss it."""
    triggers = _load_triggers_json()
    rows_by_action: dict[str, set[str]] = {}
    for rule in triggers["rules"]:
        rows_by_action.setdefault(rule["action"], set()).add(rule["agents_md_row"])
    run_rows = rows_by_action.get("run_shipgate", set()) | rows_by_action.get("force_run", set())
    skip_rows = rows_by_action.get("skip_shipgate", set())
    has_dry_run_rule = bool(rows_by_action.get("dry_run"))

    data_rows = _agents_md_trigger_table_rows()
    assert data_rows, "Could not parse the AGENTS.md trigger table."

    for trigger_text, decision in data_rows:
        decision_l = decision.lower()
        if decision_l == "yes":
            assert trigger_text in run_rows, (
                f"AGENTS.md trigger row {trigger_text!r} (decision={decision!r}) "
                "has no run_shipgate/force_run rule in docs/triggers.json. Add a "
                "rule whose agents_md_row matches this cell, or fix the table."
            )
        elif "dry-run" in decision_l:
            assert has_dry_run_rule, (
                f"AGENTS.md row {trigger_text!r} (decision={decision!r}) implies "
                "an advisory dry-run, but docs/triggers.json has no dry_run rule."
            )
        else:  # plain Skip
            assert trigger_text in skip_rows, (
                f"AGENTS.md skip row {trigger_text!r} (decision={decision!r}) has "
                "no skip_shipgate rule in docs/triggers.json."
            )


def test_triggers_evaluator_smoke():
    """Sanity-check the evaluator for the canonical positive and
    negative cases. Prevents a regression where rule semantics drift
    silently — e.g. the docs-only negative case starts firing
    `run_shipgate`."""
    docs_only = evaluate(paths=["README.md", "docs/index.md"])
    assert docs_only["run_shipgate"] is False, (
        f"Docs-only PR must not trigger Shipgate; got {docs_only!r}."
    )
    mcp_change = evaluate(paths=["tools/my_mcp.json"])
    assert mcp_change["run_shipgate"] is True, (
        f"MCP export change must trigger Shipgate; got {mcp_change!r}."
    )
    codex_plugin_change = evaluate(paths=["plugins/browser/.codex-plugin/plugin.json"])
    assert codex_plugin_change["run_shipgate"] is True, (
        f"Codex plugin manifest change must trigger Shipgate; got {codex_plugin_change!r}."
    )
    codex_config_change = evaluate(paths=[".codex/config.toml"])
    assert codex_config_change["run_shipgate"] is True, (
        f"Codex repo config change must trigger Shipgate; got {codex_config_change!r}."
    )
    decorator = evaluate(
        paths=["agent.py"],
        diff_text="+@function_tool\n+def search(): ...",
    )
    assert decorator["run_shipgate"] is True, (
        f"@function_tool decorator addition must trigger Shipgate; got {decorator!r}."
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
        f"Docs-only PR with prose-mentioned @tool must NOT trigger Shipgate; got {result!r}."
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
        diff_text=("+@function_tool\n+def stub(): pass  # used in fixtures"),
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
        f"Code+test mix with @function_tool must trigger Shipgate; got {result!r}."
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
    assert (
        _eval_predicate(
            {"every_file_matches": "**/*.md"},
            paths=["README.md", "docs/x.md"],
            diff_text="",
            manifest_present=False,
            detect_result=None,
            user_requested=False,
        )
        is True
    )

    # List form: every path matches at least one glob in the list
    assert (
        _eval_predicate(
            {"every_file_matches": ["**/*.md", "tests/**"]},
            paths=["README.md", "tests/test_foo.py"],
            diff_text="",
            manifest_present=False,
            detect_result=None,
            user_requested=False,
        )
        is True
    )

    # List form: a path matching no glob in the list returns False
    assert (
        _eval_predicate(
            {"every_file_matches": ["**/*.md", "tests/**"]},
            paths=["README.md", "src/agent.py"],
            diff_text="",
            manifest_present=False,
            detect_result=None,
            user_requested=False,
        )
        is False
    )


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
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
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
    (tmp_path / "agent.py").write_text("@function_tool\ndef foo(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "agent.py"], check=True)

    paths, diff_text = _git_diff_context(None)
    assert "agent.py" in paths, f"Staged file missing from --git-diff paths: {paths!r}"
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
    assert "new_mcp.json" in paths, f"Untracked file missing from --git-diff paths: {paths!r}"
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
    assert rule is not None, "TRIGGER-EXISTING-MANIFEST-PRESENT must remain in the catalog."
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


def test_well_known_links_to_agent_discovery_onramps():
    """Discovery metadata must keep the newer human/agent awareness
    fields wired up. Otherwise AI search and coding agents can fetch
    `.well-known` and miss the zero-install detector or per-agent
    on-ramp docs."""
    data = json.loads(_read(".well-known/agents-shipgate.json"))

    audiences = set(data.get("audiences", []))
    assert {"agent_builders", "platform_engineers", "coding_agents"} <= audiences

    when_to_use = data.get("when_to_use", [])
    assert any("n8n" in entry for entry in when_to_use), (
        ".well-known when_to_use must mention n8n tool-surface changes."
    )
    assert any("Codex plugin" in entry for entry in when_to_use), (
        ".well-known when_to_use must mention Codex plugin changes."
    )

    expected_urls = {
        "ai_search_summary_url": "/docs/ai-search-summary.md",
        "zero_install_detector_url": "/tools/shipgate-detect.py",
    }
    for key, suffix in expected_urls.items():
        url = data.get(key, "")
        assert url.startswith("https://"), f"{key} must be an absolute HTTPS URL."
        assert url.endswith(suffix), f"{key} must end with {suffix}; got {url!r}."

    onramps = data.get("agent_onramps", {})
    expected_onramps = {
        "index": "/docs/agents/README.md",
        "protocol": "/docs/agents/protocol.md",
        "target_repo_snippets": "/docs/target-repo-agent-snippets.md",
        "codex": "/docs/agents/use-with-codex.md",
        "claude_code": "/docs/agents/use-with-claude-code.md",
        "cursor": "/docs/agents/use-with-cursor.md",
        # Harness-agnostic on-ramp (Cline, Windsurf, Devin, Aider, …):
        # the machine-readable discovery map must route agents that are
        # not one of the three named harnesses, or the "start with
        # .well-known" instruction strands exactly that audience.
        "any_coding_agent": "/docs/agents/any-coding-agent.md",
    }
    for key, suffix in expected_onramps.items():
        url = onramps.get(key, "")
        assert url.startswith("https://"), f"agent_onramps.{key} must be an absolute HTTPS URL."
        assert url.endswith(suffix), f"agent_onramps.{key} must end with {suffix}; got {url!r}."


def test_well_known_advertises_agent_feedback_loop():
    """Coding agents need a safe outbound feedback path when a verifier
    result is wrong, unclear, or incomplete. Pin the redacted export and
    issue-template pointers so feedback does not depend on prose search."""
    data = json.loads(_read(".well-known/agents-shipgate.json"))
    feedback = data.get("feedback_loop", {})

    assert "missed_capability" in feedback.get("when", [])
    assert "unsafe_pass" in feedback.get("when", [])
    assert feedback.get("export_command") == data["commands"]["feedback_export"]
    assert "--redact" in feedback.get("export_command", "")
    assert feedback.get("issue_template", "").endswith(
        "/issues/new?template=agent_feedback.yml"
    )
    assert "shipgate-feedback.json" in feedback.get("attach", [])
    forbidden = set(feedback.get("do_not_attach", []))
    assert {"unredacted reports", "raw tool outputs", "secrets", "chain-of-thought"} <= (
        forbidden
    )


def test_well_known_seo_geo_positioning_fields_are_pinned():
    """AI-search discovery fields are public contract surface. Pin
    their shape so answer-engine positioning does not silently drift
    away from the AI-generated PR verifier wedge."""
    data = json.loads(_read(".well-known/agents-shipgate.json"))

    assert data.get("category") == "agent_release_readiness"
    assert data.get("primary_wedge") == "ai_generated_agent_pr_verifier"
    assert data.get("primary_use_case") == (
        "deterministic merge verdicts for AI-generated agent capability changes"
    )
    assert data.get("gating_signal") == "release_decision.decision"
    assert data.get("merge_verdicts") == [
        "mergeable",
        "human_review_required",
        "insufficient_evidence",
        "blocked",
        "unknown",
    ]

    positioning = data.get("positioning", {})
    assert positioning.get("short") == "Merge verdicts for AI-generated agent PRs"
    assert POSITIONING_PHRASE in positioning.get("answer", "")
    assert "Three Moons Lab" in positioning.get("answer", "")
    assert "deterministic merge verdict" in positioning.get("answer", "")
    assert "Codex, Claude Code, Cursor" in positioning.get("primary_use_case", "")
    assert positioning.get("not_for") == [
        "llm_evals",
        "runtime_guardrails",
        "runtime_observability",
        "general_linting",
    ]

    primary_keywords = data.get("primary_keywords", [])
    for keyword in (
        "agent release readiness",
        "Tool-Use Readiness",
        "AI agent release gate",
        "AI agent CI/CD",
        "MCP tool security",
        "OpenAPI tool scanning",
        "OpenAI Agents SDK release gate",
        "GitHub Action for AI agents",
        "AI-generated PR review",
        "agent capability merge verdict",
        "deterministic merge verdict",
    ):
        assert keyword in primary_keywords

    commands = data.get("commands", {})
    primary_commands = data.get("primary_commands", {})
    contract = build_contract_payload().model_dump(mode="json")
    assert primary_commands == contract["primary_commands"]
    assert set(primary_commands) == {
        "check_codex",
        "check_claude_code",
        "check_cursor",
        "verify_pr",
        "host_audit",
    }
    assert primary_commands["check_codex"].startswith("shipgate check ")
    assert primary_commands["check_claude_code"].startswith("shipgate check ")
    assert primary_commands["check_cursor"].startswith("shipgate check ")
    assert primary_commands["verify_pr"].startswith("agents-shipgate verify ")
    assert primary_commands["host_audit"].startswith("shipgate audit --host")
    assert "verify_local" not in primary_commands
    assert commands.get("preview") == "agents-shipgate verify --preview --json"
    assert commands.get("verify_local", "").startswith("agents-shipgate verify ")
    assert commands.get("install_ai_coding_workflow") == (
        "agents-shipgate init --workspace . --write --ci --agent-instructions=default --json"
    )
    assert data.get("check_run_policies") == [
        "advisory",
        "blocked-fails",
        "require-mergeable",
    ]
    assert (
        data.get("github_action_pr_workflow", {})
        .get("recommended_inputs", {})
        .get("diff_base")
        == "target"
    )
    assert "feedback export" in commands.get("feedback_export", "")
    assert data.get("fixture_run") == "agents-shipgate fixture run ai_generated_refund_pr"
    assert data.get("static_scan_fixture_run") == (
        "agents-shipgate fixture run support_refund_agent"
    )
    assert data.get("verifier_read_order", [])[:7] == [
        "merge_verdict",
        "applicability",
        "can_merge_without_human",
        "first_next_action",
        "fix_task",
        "capability_review.top_changes",
        "agent_controller",
    ]
    assert data.get("supporting_provisional_surfaces", []) == [
        "agent_result",
        "agent_decision",
        "release_evidence_packet",
        "reviewer_summary",
        "verifier_summary",
        "capability_review",
        "runtime_trace_evidence",
        "capability_diff_projections",
        "skill_review",
    ]

    recommended_topics = data.get("recommended_github_topics", [])
    for topic in (
        "ai-agents",
        "agent-release-readiness",
        "tool-use-readiness",
        "mcp",
        "model-context-protocol",
        "openapi",
        "openai-agents-sdk",
        "github-actions",
        "static-analysis",
        "sarif",
    ):
        assert topic in recommended_topics
    assert all("_" not in topic for topic in recommended_topics), (
        "recommended_github_topics must use GitHub topic slug kebab-case."
    )


def test_prominent_surfaces_only_promote_check_verify_and_host_audit():
    """First-look surfaces must not promote supporting setup commands."""

    forbidden = (
        "agents-shipgate detect",
        "agents-shipgate init",
        "agents-shipgate scan",
        "agents-shipgate preflight",
        "agents-shipgate bootstrap",
        "agents-shipgate apply-patches",
    )
    readme = _read("README.md")
    readme_top = readme.split("## Verify-first quickstart", 1)[1].split(
        "## How to read your first result", 1
    )[0]
    quickstart = _read("docs/quickstart.md")
    quickstart_top = quickstart.split("## Verify-first quickstart", 1)[1].split(
        "## Supporting zero-install relevance check", 1
    )[0]
    slash = _read(".claude/commands/shipgate.md")
    slash_commands = slash.split("Prominent commands:", 1)[1].split(
        "Required behavior", 1
    )[0]
    target_snippets = _read("docs/target-repo-agent-snippets.md")
    agents_block = target_snippets.split("## `AGENTS.md`", 1)[1].split(
        "## Codex Skill", 1
    )[0]
    claude_block = target_snippets.split("## `CLAUDE.md`", 1)[1].split(
        "## `.cursor/rules/agents-shipgate.mdc`", 1
    )[0]
    cursor_block = target_snippets.split("## `.cursor/rules/agents-shipgate.mdc`", 1)[
        1
    ].split("## `.github/pull_request_template.md`", 1)[0]

    surfaces = {
        "README quickstart": readme_top,
        "docs/quickstart": quickstart_top,
        "slash prominent commands": slash_commands,
        "target AGENTS snippet": agents_block,
        "target CLAUDE snippet": claude_block,
        "target Cursor snippet": cursor_block,
    }
    for name, text in surfaces.items():
        for command in forbidden:
            assert command not in text, f"{name} promotes supporting command {command!r}"

    well_known = json.loads(_read(".well-known/agents-shipgate.json"))
    contract = build_contract_payload().model_dump(mode="json")
    for name, command in {
        **well_known["primary_commands"],
        **contract["primary_commands"],
    }.items():
        expected_prefixes = (
            "shipgate check ",
            "agents-shipgate verify ",
            "shipgate audit --host",
        )
        assert any(command.startswith(prefix) for prefix in expected_prefixes), (
            f"{name} is not one of the prominent flows: {command}"
        )
        for forbidden_command in forbidden:
            assert forbidden_command not in command


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
    return sorted(p.name for p in _PROMPT_DIR.glob("*.md") if p.name not in _PROMPT_MIRROR_EXCLUDE)


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


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES + ("docs/ai-search-summary.md",))
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


@pytest.mark.parametrize("relpath", POSITIONING_SURFACES)
def test_primary_surfaces_use_mvp_wedge_positioning(relpath):
    """Primary adoption and metadata surfaces must describe the current
    MVP wedge, not the broader agent-lifecycle roadmap."""
    text = _normalize_ws(_read(relpath)).lower()
    assert POSITIONING_PHRASE.lower() in text, (
        f"{relpath} must use the current MVP positioning phrase {POSITIONING_PHRASE!r}."
    )


def test_scan_help_uses_tool_use_readiness_positioning():
    text = _normalize_ws(_read("src/agents_shipgate/cli/_register_scan.py"))
    assert POSITIONING_SCAN_DOCSTRING.lower() in text.lower(), (
        "scan command docstring must use the canonical merge-gate positioning phrase."
    )


def test_structured_metadata_fields_use_mvp_wedge_positioning():
    well_known = json.loads(_read(".well-known/agents-shipgate.json"))
    assert well_known["tagline"] == POSITIONING_PHRASE

    pyproject = tomllib.loads(_read("pyproject.toml"))
    description = pyproject["project"]["description"]
    assert description.startswith(f"{POSITIONING_PHRASE}.")


def test_pyproject_keywords_stay_wedge_focused():
    """PyPI keywords render as public project tags, so keep them
    focused on the Tool-Use Readiness wedge instead of broader category
    or duplicated CI/CD phrasing."""
    pyproject = tomllib.loads(_read("pyproject.toml"))
    keywords = set(pyproject["project"]["keywords"])

    assert "agent-governance-infrastructure" not in keywords
    assert "agent-governance" not in keywords
    assert not {"agent-cicd", "ai-agent-cicd"} <= keywords, (
        "Use one CI/CD keyword so the visible PyPI tag set stays focused."
    )


def test_report_and_packet_disclaimers_use_mvp_wedge_positioning():
    assert "advisory" in DISCLAIMER.lower()
    assert "advisory" in PACKET_NON_PROOF_HEADLINE.lower()
    assert POSITIONING_PHRASE.lower() in DISCLAIMER.lower()
    assert POSITIONING_PHRASE.lower() in PACKET_NON_PROOF_HEADLINE.lower()


@pytest.mark.parametrize(
    "phrase",
    [
        "healthcare for agents",
        "healthcare-for-agents",
        "agent lifecycle readiness",
        "agent-lifecycle readiness",
        "agent-lifecycle-readiness",
        "governance platform",
        "governance-platform",
        "enterprise governance",
        "enterprise-governance",
        "across the agent lifecycle",
        "across-the-agent-lifecycle",
    ],
)
def test_broad_positioning_pattern_catches_space_and_hyphen_variants(phrase):
    assert BROAD_POSITIONING_PATTERN.search(phrase)


@pytest.mark.parametrize("relpath", PRIMARY_POSITIONING_SURFACES)
def test_primary_surfaces_do_not_claim_broad_agent_healthcare(relpath):
    """The MVP public surface is Tool-Use Readiness. Broader healthcare,
    lifecycle, and enterprise-governance language belongs in explicitly
    roadmap/thesis material, not adoption paths."""
    text = _read(relpath)
    match = BROAD_POSITIONING_PATTERN.search(text)
    assert match is None, (
        f"{relpath} uses broad positioning phrase {match.group(0)!r}. "
        "Keep primary surfaces focused on the Tool-Use Readiness static "
        "release gate; put lifecycle/healthcare/governance-platform "
        "language only in roadmap or thesis material."
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
    "TRIGGER-CODEX-PLUGIN-CHANGED": [
        ".codex-plugin/plugin.json",
        "plugins/browser-use/.codex-plugin/plugin.json",
        ".agents/plugins/marketplace.json",
        "plugins/browser-use/.app.json",
        "plugins/browser-use/.mcp.json",
        "plugins/browser-use/skills/browser/SKILL.md",
    ],
    "TRIGGER-CODEX-BOUNDARY-CONFIG-CHANGED": [
        ".codex/config.toml",
        "packages/agent/.codex/config.toml",
        ".codex/hooks.json",
        "packages/agent/.codex/hooks.json",
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


def test_self_dogfood_manifest_scans_codex_plugin_package() -> None:
    """The internal self-dogfood gate must stay on supported static surfaces.

    It intentionally scans the shipped Codex plugin package, not the Python
    scanner implementation. Scanner-source assurance lives in normal CI.
    """
    import yaml

    manifest = yaml.safe_load(_read("shipgate-self.yaml"))
    assert manifest["tool_sources"] == [
        {
            "id": "agents_shipgate_codex_plugin_package",
            "type": "codex_plugin",
            "mode": "package",
            "path": "plugins/agents-shipgate",
        }
    ]
    assert manifest["output"]["directory"] == "agents-shipgate-reports/self"

    workflow = _read(".github/workflows/agents-shipgate-self.yml")
    assert "config: shipgate-self.yaml" in workflow
    assert "verify_mode: verify" in workflow
    assert "fail_on_merge_verdicts: blocked" in workflow


def test_pre_commit_local_docs_show_same_path_trigger_clauses():
    """The copy-paste `repo: local` snippet must not lag the root hook.

    Downstream users often copy the local snippet directly instead of using
    the canonical `repo: https://...` install form, so the documented regex
    needs the same path-based trigger clauses as `.pre-commit-hooks.yaml`.
    """
    text = _read("docs/integrations.md")
    for clause in (
        r".*swagger.*\.(yaml|yml|json)",
        r"(.*/)?\.codex/(config\.toml|hooks\.json)",
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


# ---------------------------------------------------------------------------
# Supported-input alignment (P0.1)
# ---------------------------------------------------------------------------
#
# SUPPORTED_INPUTS in agents_shipgate.schemas.contract is the in-code
# source of truth. Every public surface that lists supported inputs must
# (a) mention every enum-id and (b) not mention anything unknown.
# Adapter ClassVars are pinned bidirectionally against the constant so a
# new adapter cannot land without updating SUPPORTED_INPUTS, and a
# docs-only addition cannot land without a backing adapter (the n8n
# failure mode that motivated this block).

# Build longest-first so "Anthropic Messages API" beats "Anthropic"
# when both appear in the same token.
_ALIAS_TO_ENUM: dict[str, str] = {
    alias: enum_id for enum_id, aliases in SUPPORTED_INPUTS.items() for alias in aliases
}
_ALIASES_LONGEST_FIRST: list[str] = sorted(_ALIAS_TO_ENUM, key=len, reverse=True)

# Internal-only adapters that intentionally never appear in user-facing
# input lists. Adding to this set requires a comment justifying why
# the adapter isn't a SUPPORTED_INPUTS key.
INTERNAL_ADAPTERS: set[str] = {
    # `validation` is the catch-all adapter for shipgate.yaml-only
    # manifest entries; it's never a user-facing input source.
    "validation",
}


def _resolve_alias(token: str) -> str | None:
    """Return the enum-id matching the longest alias that appears as
    a substring of `token`, or None if nothing matches."""
    clean = token.strip().rstrip(",.;:")
    for alias in _ALIASES_LONGEST_FIRST:
        if alias in clean:
            return _ALIAS_TO_ENUM[alias]
    return None


def _slice_section(text: str, start_marker: str, end_marker: str) -> str:
    """Slice between `start_marker` (inclusive of length) and the next
    occurrence of `end_marker`. Returns the tail if `end_marker` is
    absent."""
    start = text.index(start_marker) + len(start_marker)
    end = text.find(end_marker, start)
    return text[start:] if end == -1 else text[start:end]


def _resolve_tokens(tokens: list[str]) -> tuple[set[str], list[str]]:
    resolved: set[str] = set()
    unresolved: list[str] = []
    for token in tokens:
        enum_id = _resolve_alias(token)
        if enum_id is None:
            unresolved.append(token)
        else:
            resolved.add(enum_id)
    return resolved, unresolved


def _resolve_freeform(text: str) -> tuple[set[str], list[str]]:
    """Substring-scan freeform prose for every alias. Unresolved is
    always empty for prose — the bidirectional adapter test plus the
    structured extractors below catch unknown-input drift on surfaces
    that have a parseable list shape."""
    resolved: set[str] = set()
    for alias in _ALIASES_LONGEST_FIRST:
        if alias in text:
            resolved.add(_ALIAS_TO_ENUM[alias])
    return resolved, []


def _well_known_input_ids(text: str) -> tuple[set[str], list[str]]:
    ids = json.loads(text)["inputs"]
    resolved = {i for i in ids if i in SUPPORTED_INPUTS}
    unresolved = [i for i in ids if i not in SUPPORTED_INPUTS]
    return resolved, unresolved


def _llms_txt_inputs(text: str) -> tuple[set[str], list[str]]:
    block = _slice_section(text, "## Inputs", "\n## ")
    tokens = re.findall(r"^- (.+?)\.?\s*$", block, flags=re.M)
    return _resolve_tokens(tokens)


def _agents_md_inputs_bullet(text: str) -> tuple[set[str], list[str]]:
    m = re.search(r"\*\*Inputs:\*\*\s+(.+)", text)
    if not m:
        return set(), ["<no Inputs bullet found>"]
    return _resolve_tokens([t.strip() for t in m.group(1).split("·")])


def _readme_inputs_table(text: str) -> tuple[set[str], list[str]]:
    rows = re.findall(r"^\|\s*([^|]+?)\s*\|\s*Supported\s*\|", text, flags=re.M)
    return _resolve_tokens(rows)


def _pyproject_description(text: str) -> tuple[set[str], list[str]]:
    m = re.search(r'^description\s*=\s*"([^"]+)"', text, flags=re.M)
    if not m:
        return set(), ["<no description>"]
    return _resolve_freeform(m.group(1))


def _action_yml_description(text: str) -> tuple[set[str], list[str]]:
    m = re.search(r"^description:\s*>-\n((?:\s{2,}.+\n)+)", text, flags=re.M)
    if not m:
        return set(), ["<no description>"]
    return _resolve_freeform(m.group(1))


def _faq_inputs_block(text: str) -> tuple[set[str], list[str]]:
    block = _slice_section(text, "## What inputs does it support?", "\n## ")
    tokens = re.findall(r"^- (.+)$", block, flags=re.M)
    return _resolve_tokens(tokens)


def _markdown_supported_inputs_section(text: str) -> tuple[set[str], list[str]]:
    """`## Supported inputs` markdown section → `- bullet` tokens.
    Used by docs/overview.md and docs/ai-search-summary.md."""
    block = _slice_section(text, "## Supported inputs", "\n## ")
    tokens = re.findall(r"^- (.+?)\.?\s*$", block, flags=re.M)
    return _resolve_tokens(tokens)


def _readme_intro_inputs(text: str) -> tuple[set[str], list[str]]:
    """README intro paragraph (the `It scans …` sentence under the
    one-line positioning header). Prose, so only the missing direction
    is enforced — `extra` tokens are the bidirectional adapter test's
    job."""
    m = re.search(
        r"It scans\s+(.+?\.)",
        text,
        flags=re.DOTALL,
    )
    if not m:
        return set(), ["<no `It scans` intro sentence found>"]
    return _resolve_freeform(m.group(1))


def _faq_tool_surface_paragraph(text: str) -> tuple[set[str], list[str]]:
    """`## What is an AI agent tool surface?` answer in docs/faq.md
    enumerates the same input set in prose. Pin it so it can't drift
    away from the canonical `## What inputs does it support?` bullet
    list lower down the same page."""
    block = _slice_section(text, "## What is an AI agent tool surface?", "\n## ")
    return _resolve_freeform(block)


def _skill_md_intro(text: str) -> tuple[set[str], list[str]]:
    """The first paragraph of skills/agents-shipgate/SKILL.md
    enumerates the tool-source list inside parentheses. Prose scan."""
    m = re.search(r"tool sources \(([^)]+)\)", text)
    if not m:
        return set(), ["<no `tool sources (…)` parenthetical found>"]
    return _resolve_freeform(m.group(1))


def _design_partners_good_fit_bullet(text: str) -> tuple[set[str], list[str]]:
    """`## Good Fit` first bullet enumerates the input set. Prose."""
    m = re.search(
        r"- Ships agents that call tools through\s+(.+?\.)",
        text,
        flags=re.DOTALL,
    )
    if not m:
        return set(), ["<no `Ships agents that call tools through …` bullet found>"]
    return _resolve_freeform(m.group(1))


INPUT_SURFACES: tuple[tuple[str, object], ...] = (
    (".well-known/agents-shipgate.json", _well_known_input_ids),
    ("llms.txt", _llms_txt_inputs),
    ("AGENTS.md", _agents_md_inputs_bullet),
    ("README.md", _readme_inputs_table),
    ("README.md", _readme_intro_inputs),
    ("pyproject.toml", _pyproject_description),
    ("action.yml", _action_yml_description),
    ("docs/faq.md", _faq_inputs_block),
    ("docs/faq.md", _faq_tool_surface_paragraph),
    ("docs/overview.md", _markdown_supported_inputs_section),
    ("docs/ai-search-summary.md", _markdown_supported_inputs_section),
    ("skills/agents-shipgate/SKILL.md", _skill_md_intro),
    ("docs/design-partners.md", _design_partners_good_fit_bullet),
)


@pytest.mark.parametrize("relpath,extractor", INPUT_SURFACES)
def test_public_surface_lists_every_supported_input(relpath, extractor):
    """Every supported-input surface must list each SUPPORTED_INPUTS
    enum-id (via at least one alias) and must not list any unknown
    tokens. Catches the n8n failure mode where a runtime adapter ships
    but discovery and docs surfaces miss it."""
    observed, unresolved = extractor(_read(relpath))
    missing = set(SUPPORTED_INPUTS) - observed
    assert not missing, (
        f"{relpath} missing input enum-ids: {sorted(missing)}. "
        f"Bump the surface or update SUPPORTED_INPUTS in "
        f"src/agents_shipgate/schemas/contract.py."
    )
    assert not unresolved, (
        f"{relpath} mentions tokens not in SUPPORTED_INPUTS: "
        f"{unresolved}. Either add the input as an adapter + "
        f"SUPPORTED_INPUTS entry, or remove the stray reference."
    )


def test_well_known_inputs_order_matches_constant():
    """Beyond set equality: the wire-stable `inputs` array order must
    match the declared order of SUPPORTED_INPUTS so external consumers
    that index positionally see a stable contract."""
    data = json.loads(_read(".well-known/agents-shipgate.json"))
    assert data["inputs"] == list(SUPPORTED_INPUTS), (
        f"`.well-known/agents-shipgate.json::inputs` order drift: got "
        f"{data['inputs']}, expected {list(SUPPORTED_INPUTS)}."
    )


def test_supported_inputs_match_adapter_class_vars_bidirectionally():
    """Bidirectional: every adapter `source_type` ClassVar (minus
    INTERNAL_ADAPTERS) must be in SUPPORTED_INPUTS — catches a new
    adapter added without updating the contract (the exact n8n
    failure mode that motivated this test). And every SUPPORTED_INPUTS
    key must have a backing adapter — catches a docs-only addition
    without runtime support."""
    adapter_dir = REPO_ROOT / "src" / "agents_shipgate" / "inputs"
    pattern = re.compile(r'source_type:\s*ClassVar\[str\]\s*=\s*"([^"]+)"')
    adapter_ids: set[str] = set()
    # ``rglob`` so adapters in sub-packages (e.g. ``inputs/n8n/_adapter.py``
    # after the v0.21 E8 decomposition) are still scanned. Skip
    # ``__pycache__`` defensively.
    for path in adapter_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        adapter_ids.update(pattern.findall(path.read_text(encoding="utf-8")))
    user_facing = adapter_ids - INTERNAL_ADAPTERS
    assert user_facing == set(SUPPORTED_INPUTS), (
        f"SUPPORTED_INPUTS vs adapter ClassVars disagree.\n"
        f"  Missing from SUPPORTED_INPUTS: "
        f"{sorted(user_facing - set(SUPPORTED_INPUTS))}\n"
        f"  Missing adapter: "
        f"{sorted(set(SUPPORTED_INPUTS) - user_facing)}\n"
        f"Adapter ClassVars are the runtime source of truth."
    )


# ---------------------------------------------------------------------------
# Trust-claim qualifier coverage (P0.1)
# ---------------------------------------------------------------------------

TRUST_CLAIM_PATTERN = re.compile(
    r"\b(no\s+LLM\s+calls?|no\s+tool\s+calls?|no\s+agent\s+execution|"
    r"no\s+network(?:\s+access|\s+calls?)?|"
    r"no\s+MCP\s+server\s+connections|"
    r"does\s+not\s+(?:invoke|run|call|execute|connect)|no\s+telemetry)\b",
    re.IGNORECASE,
)
TRUST_QUALIFIER_PATTERN = re.compile(
    r"\b(by\s+default|static[-\s]by[-\s]default|"
    r"audited\s+exceptions?|allowlist(?:ed)?|"
    r"ALLOWED_EXCEPTIONS|meta-CLI|bootstrap\s+chain)\b",
    re.IGNORECASE,
)
TRUST_QUALIFIER_WINDOW = 400  # ~one paragraph; matches CONTEXT_WINDOW

TRUST_CLAIM_SURFACES = (
    "README.md",
    "AGENTS.md",
    "STABILITY.md",
    "llms.txt",
    "action.yml",
    "docs/faq.md",
    "docs/agent-recipes.md",
    "docs/trust-model.md",
)


@pytest.mark.parametrize("relpath", TRUST_CLAIM_SURFACES)
def test_trust_claims_carry_meta_cli_qualifier(relpath):
    """Any 'no LLM calls / no agent execution / does not call …' style
    claim in a public surface must sit within ~one paragraph of a
    qualifier (`by default`, `static-by-default`, `ALLOWED_EXCEPTIONS`,
    `audited exceptions`, `meta-CLI`, `bootstrap chain`). Unqualified
    claims mislead coding agents that read the surface and conclude
    Shipgate never shells out."""
    text = _read(relpath)
    for match in TRUST_CLAIM_PATTERN.finditer(text):
        window = text[
            max(0, match.start() - TRUST_QUALIFIER_WINDOW) : match.end() + TRUST_QUALIFIER_WINDOW
        ]
        assert TRUST_QUALIFIER_PATTERN.search(window), (
            f"{relpath}:{text.count(chr(10), 0, match.start()) + 1} "
            f"makes an unqualified trust claim {match.group(0)!r}. Add "
            f"'by default' / 'static-by-default' nearby, or reference "
            f"ALLOWED_EXCEPTIONS (see STABILITY.md §Meta-CLI surfaces)."
        )


_META_CLI_SECTION_PATTERN = re.compile(r"Meta-CLI\s+surfaces\s+\(allowlisted,\s+audited\)")


def test_stability_md_pins_canonical_meta_cli_section_exactly_once():
    """STABILITY.md is the canonical owner of the meta-CLI exception
    list. The 'Meta-CLI surfaces (allowlisted, audited)' header must
    appear exactly once (so other surfaces link or summarize rather
    than restate), and it must point at ALLOWED_EXCEPTIONS by name —
    rather than enumerate a partial list that drifts when the test
    file changes."""
    text = _read("STABILITY.md")
    matches = list(_META_CLI_SECTION_PATTERN.finditer(text))
    assert len(matches) == 1, (
        f"STABILITY.md must declare 'Meta-CLI surfaces (allowlisted, "
        f"audited)' exactly once; found {len(matches)}."
    )
    section = text[matches[0].start() : matches[0].start() + 2500]
    assert "ALLOWED_EXCEPTIONS" in section, (
        "Meta-CLI section must reference "
        "tests/test_adapter_static_only.py::ALLOWED_EXCEPTIONS so the "
        "audited list lives in code, not duplicated prose."
    )


# ---------------------------------------------------------------------------
# action.yml legacy-status annotation (P0.1)
# ---------------------------------------------------------------------------


def test_action_yml_status_output_marked_legacy():
    """outputs.status must carry a 'legacy / v0.7 caller / baseline-blind
    / prefer release_decision' annotation in a YAML comment immediately
    above or in the description string. The existing prose-level
    test_public_surface_does_not_recommend_summary_status_for_gating
    relies on a 400-char window, which doesn't reach across the
    structured YAML block — this test pins the annotation explicitly."""
    text = _read("action.yml")
    m = re.search(
        r"outputs:\n(?:[^\n]*\n){0,4}\s*status:\n"
        r"(?:\s*#[^\n]*\n)*"
        r"\s*description:\s*([^\n]+)\n",
        text,
    )
    assert m is not None, "outputs.status block not found in action.yml"
    block = text[max(0, m.start() - 200) : m.end()]
    assert re.search(
        r"legacy|v0\.7\s+caller|baseline-blind|prefer.*release_decision",
        block,
        re.IGNORECASE,
    ), (
        "action.yml outputs.status must carry a 'legacy / v0.7 caller / "
        "baseline-blind / prefer release_decision' annotation so CI "
        "consumers don't gate on it."
    )


# ---------------------------------------------------------------------------
# Prose schema-reference drift guard (P0.1)
# ---------------------------------------------------------------------------

CURRENT_SCHEMA_PROSE_PATTERN = re.compile(
    r"schema\s+v(\d+\.\d+)\s*,?\s*current",
    re.IGNORECASE,
)


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES)
def test_prose_current_schema_references_match_runtime(relpath):
    """Any prose phrase like 'schema v0.X, current' must match the
    runtime report schema. Catches the FAQ-style drift that the
    filename-based tests miss (e.g. `schema v0.17, current` while the
    current report schema is v0.18)."""
    text = _read(relpath)
    for match in CURRENT_SCHEMA_PROSE_PATTERN.finditer(text):
        version = match.group(1)
        assert version == CURRENT_REPORT_SCHEMA_VERSION, (
            f"{relpath} prose claims 'schema v{version}, current'; "
            f"runtime is v{CURRENT_REPORT_SCHEMA_VERSION}."
        )


# ---------------------------------------------------------------------------
# Singular-underscore module name (P0.1, tightens forbidden-name coverage)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("relpath", PUBLIC_SURFACES)
def test_no_singular_underscore_module_name(relpath):
    """`agent_shipgate` (singular underscore) is always wrong — the
    correct Python module is `agents_shipgate` (plural). Caught
    separately from FORBIDDEN_NAME_PATTERN because Python contexts
    legitimately need `agents_shipgate` to pass."""
    text = _read(relpath)
    for m in SINGULAR_UNDERSCORE_PATTERN.finditer(text):
        line = text[: m.start()].count("\n") + 1
        raise AssertionError(
            f"{relpath}:{line} uses singular `agent_shipgate`; the "
            f"correct Python module is `agents_shipgate` (plural)."
        )


# ---------------------------------------------------------------------------
# Read-first artifact vs the runtime contract's agent_read_order
# ---------------------------------------------------------------------------

READ_FIRST_PATTERN = re.compile(r"[Rr]ead\s+`([^`]+)`\s+first")
# Prose surfaces that tell a coding agent which verify artifact to read
# first. .well-known/agents-shipgate.json carries the machine-readable
# agent_read_order (validated against the contract elsewhere in this
# file); these are the human/agent prose mirrors of that order.
READ_FIRST_SURFACES = (
    "README.md",
    "AGENTS.md",
    "llms.txt",
    ".claude/commands/shipgate.md",
)


@pytest.mark.parametrize("relpath", READ_FIRST_SURFACES)
def test_read_first_instructions_match_contract_agent_read_order(relpath):
    """Every 'Read `<artifact>` first' instruction must name the first
    artifact in the runtime contract's agent_read_order (agent-handoff.json
    since contract v7), optionally with a reports-dir prefix or a field
    path suffix. README shipped both 'read verifier.json first' and
    'read agent-handoff.json first' simultaneously until v0.14.x; this
    pins the prose surfaces to the contract so the contradiction cannot
    return. verifier.json stays the authoritative controller substrate —
    mentioning it is fine, telling an agent to read it *first* is not."""
    contract = build_contract_payload().model_dump(mode="json")
    first_artifact = contract["agent_read_order"][0]
    assert first_artifact == "agent-handoff.json", (
        "contract agent_read_order[0] changed; sweep the read-first "
        "prose on READ_FIRST_SURFACES, then update this pin."
    )
    text = _read(relpath)
    matches = READ_FIRST_PATTERN.findall(text)
    assert matches, (
        f"{relpath} no longer contains a 'Read `<artifact>` first' "
        "instruction. Either restore one naming "
        f"{first_artifact} or drop the surface from READ_FIRST_SURFACES."
    )
    for artifact in matches:
        assert first_artifact in artifact, (
            f"{relpath} tells an agent to read {artifact!r} first; the "
            f"contract's agent_read_order starts with {first_artifact!r} "
            "(see docs/agent-contract-current.md § Two read entry points)."
        )
