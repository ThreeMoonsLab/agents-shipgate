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
from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.dependency_manifests import (
    DEPENDENCY_MANIFEST_GLOBS,
    is_dependency_manifest,
)
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
from agents_shipgate.triggers import (
    VALID_SURFACE_CLASSES,
    evaluate,
    load_triggers,
    result_has_surface_class,
)

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
# Prompts the adoption kits render. Their runner pin and contract floor come
# from the same build, so they track that build rather than the latest
# published release — a CI example, which installs from PyPI, still tracks the
# release.
RENDERED_PROMPT_PIN_FILES = (
    "prompts/add-shipgate-to-repo.md",
    "prompts/decide-shipgate-relevance.md",
    "skills/agents-shipgate/prompts/add-shipgate-to-repo.md",
    "skills/agents-shipgate/prompts/decide-shipgate-relevance.md",
    "plugins/claude-code/skills/agents-shipgate/prompts/add-shipgate-to-repo.md",
    "plugins/claude-code/skills/agents-shipgate/prompts/decide-shipgate-relevance.md",
)
ACTION_PIN_FILES = (
    *(s for s in PUBLIC_SURFACES if s not in RENDERED_PROMPT_PIN_FILES),
    "docs/integrations.md",
    "docs/quickstart.md",
    "docs/target-repo-agent-snippets.md",
    "examples/github-actions/01-advisory-pr-comment.yml",
    "examples/github-actions/02-strict-on-critical.yml",
    "examples/github-actions/03-strict-with-baseline.yml",
    "examples/github-actions/04-multi-config-workspace.yml",
    "examples/github-actions/05-sarif-to-code-scanning.yml",
    "examples/github-actions/07-block-on-blocked-verdict.yml",
    "examples/github-actions/08-require-mergeable.yml",
    "examples/circleci/01-advisory.yml",
    "examples/circleci/02-strict-with-baseline.yml",
    "examples/circleci/03-sarif-artifact-retention.yml",
    "examples/circleci/04-multi-config-workspace.yml",
    "examples/gitlab-ci/01-advisory.yml",
    "examples/gitlab-ci/02-strict-with-baseline.yml",
    "examples/gitlab-ci/03-sarif-or-artifact.yml",
    "examples/gitlab-ci/04-multi-config-workspace.yml",
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
    assert data.get("release_status", {}).get("latest_release") == (f"v{LATEST_PUBLISHED_VERSION}")
    outputs = data.get("outputs", [])
    for expected in (
        "packet_md",
        "packet_json",
        "packet_html",
        "capability_lock_diff_md",
        "feedback_json",
        "attestation_json",
        "org_evidence_bundle_json",
        "verification_plan_json",
        "verification_unit_result_json",
        "verification_artifact_manifest_json",
        "verification_receipt_json",
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
    assert data.get("passed_verdict_contract", "").endswith("/docs/passed-verdict-contract.md")
    assert data.get("report_schema_version") == contract["report_schema_version"]
    assert data.get("packet_schema_version") == contract["packet_schema_version"]
    assert data.get("agent_result_schema_version") == contract["agent_result_schema_version"]
    assert data.get("agent_result_schema_path") == contract["agent_result_schema_path"]
    assert data.get("agent_result_control_fields") == contract["agent_result_control_fields"]
    assert data.get("verifier_schema_version") == contract["verifier_schema_version"]
    assert data.get("verify_run_schema_version") == contract["verify_run_schema_version"]
    assert (
        data.get("verification_plan_schema_version") == contract["verification_plan_schema_version"]
    )
    assert (
        data.get("verification_unit_result_schema_version")
        == contract["verification_unit_result_schema_version"]
    )
    assert (
        data.get("verification_artifact_manifest_schema_version")
        == contract["verification_artifact_manifest_schema_version"]
    )
    assert (
        data.get("verification_receipt_schema_version")
        == contract["verification_receipt_schema_version"]
    )
    assert (
        data.get("human_authorization_request_schema_version")
        == contract["human_authorization_request_schema_version"]
    )
    assert (
        data.get("human_authorization_schema_version")
        == contract["human_authorization_schema_version"]
    )
    assert (
        data.get("human_authorization_evaluation_schema_version")
        == contract["human_authorization_evaluation_schema_version"]
    )
    assert (
        data.get("human_authorization_trust_policy_schema_version")
        == contract["human_authorization_trust_policy_schema_version"]
    )
    assert data.get("human_authorization_trust_policy_default_path") == (
        contract["human_authorization_trust_policy_default_path"]
    )
    assert (
        data.get("human_authorization_schema_path")
        == contract["human_authorization_schema_path"]
    )
    assert data.get("agent_handoff_schema_version") == contract["agent_handoff_schema_version"]
    assert data.get("agent_handoff_schema_path") == contract["agent_handoff_schema_path"]
    assert data.get("agent_handoff_artifact") == contract["agent_handoff_artifact"]
    assert (
        data.get("codex_boundary_result_schema_version")
        == (contract["codex_boundary_result_schema_version"])
    )
    assert data.get("capability_standard_version") == CAPABILITY_STANDARD_VERSION
    assert (
        data.get("capability_lock_schema_version") == (contract["capability_lock_schema_version"])
    )
    assert (
        data.get("capability_lock_diff_schema_version")
        == (contract["capability_lock_diff_schema_version"])
    )
    assert data.get("agent_read_order") == contract["agent_read_order"]
    assert data.get("verifier_read_order") == contract["verifier_read_order"]
    assert data.get("do_not_auto_assert") == contract["do_not_auto_assert"]
    assert "human_authorization" in contract["external_integration_surfaces"]
    assert "action_effect" in contract["do_not_auto_assert"]
    assert "action_authority" in contract["do_not_auto_assert"]
    assert data.get("agent_interface_operations") == contract["agent_interface_operations"]
    assert data.get("exit_code_policy") == contract["exit_code_policy"]
    assert data.get("mcp_tools") == contract["mcp_tools"]
    commands = data.get("commands", {})
    assert commands.get("agent_check_codex") == contract["commands"]["agent_check_codex"]
    assert (
        commands.get("agent_check_claude_code") == (contract["commands"]["agent_check_claude_code"])
    )
    assert commands.get("agent_check_cursor") == contract["commands"]["agent_check_cursor"]
    artifacts = data.get("artifacts", {})
    assert artifacts.get("local_contract") == (".shipgate/agent-contract.json")
    assert artifacts.get("verify_run") == contract["artifacts"]["verify_run"]
    assert artifacts.get("agent_handoff") == contract["artifacts"]["agent_handoff"]
    assert artifacts.get("verification_receipt") == (contract["artifacts"]["verification_receipt"])
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
    assert (
        data.get("org_evidence_bundle_schema_version")
        == (contract["org_evidence_bundle_schema_version"])
    )
    assert (
        data.get("host_grants_inventory_schema_version")
        == (contract["host_grants_inventory_schema_version"])
    )
    registry_url = schemas.get("registry", "")
    assert f"registry-schema.v{REGISTRY_SCHEMA_VERSION}.json" in registry_url, (
        ".well-known schemas.registry must point to the current "
        f"registry schema; got {registry_url!r}."
    )
    bundle_url = schemas.get("org_evidence_bundle", "")
    assert "org-evidence-bundle-schema.v2.json" in bundle_url
    assert data.get("org_evidence_bundle_schema_version") == (ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION)
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
    assert "verify_run" in schemas and "verify-run-schema.v4.json" in schemas["verify_run"]
    assert "verification_plan" in schemas
    assert "verification-plan-schema.v1.json" in schemas["verification_plan"]
    assert "verification_unit_result" in schemas
    assert "verification-unit-result-schema.v1.json" in schemas["verification_unit_result"]
    assert "verification_artifact_manifest" in schemas
    assert (
        "verification-artifact-manifest-schema.v1.json" in schemas["verification_artifact_manifest"]
    )
    assert "verification_receipt" in schemas
    assert "verification-receipt-schema.v1.json" in schemas["verification_receipt"]
    assert "human_authorization" in schemas
    assert "human-authorization-schema.v1.json" in schemas["human_authorization"]
    assert "agent_handoff" in schemas and "agent-handoff-schema.v7.json" in schemas["agent_handoff"]
    assert (
        "codex_boundary_result" in schemas
        and "codex-boundary-result-schema.v2.json" in schemas["codex_boundary_result"]
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
    assert stamp.group("date") == "2026-07-13", (
        "docs/architecture.md contract-check date must stay pinned to "
        "2026-07-13 until a deliberate architecture-doc refresh moves it."
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
    assert well_known["release_status"]["latest_release"] == (f"v{LATEST_PUBLISHED_VERSION}")

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
    assert ".well-known/agents-shipgate.json" in workflow
    assert '["release_status"]["latest_release"]' in workflow
    assert "refs/tags/${latest_release}" in workflow
    assert "refs/tags/v${version}" not in workflow


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
    must equal the version of the build that emitted it.

    The runner pin and the contract floor a prompt demands are rendered from
    the same build (``{{ shipgate_version }}`` /
    ``{{ minimum_control_contract_version }}``), so they cannot drift apart.
    Pinning the latest *published* release instead is what produced the
    contradiction the rendered-prompt guard prevents."""
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
    ``ACTION_PIN_PATTERN``. Like the pip pin, this tracks the emitting build
    rather than the latest published release, so the pin always satisfies the
    contract floor rendered beside it."""
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
    """Walk the CLI's emit sites and return every literal error kind.

    Every literal string passed as the first argument to
    `emit_agent_mode_error(...)` or to `_emit_input_error(...)` (the
    apply-patches helper that uses the same one-line stderr format).
    Source-of-truth for which kinds the runtime actually emits.

    The repository launcher is included because it is one of those sites: it
    reports an environment that cannot start Shipgate at all, which is the one
    failure no code under `src/` can be running to report (#334). Scanning only
    `src/` would let that kind stay out of the published catalog forever.
    """
    pattern = re.compile(
        r"(?:emit_agent_mode_error|_emit_input_error)\(\s*\n?\s*\"([a-z_]+)\"",
        re.MULTILINE,
    )
    sources = [
        *(REPO_ROOT / "src" / "agents_shipgate" / "cli").rglob("*.py"),
        REPO_ROOT / "shipgate",
    ]
    kinds: set[str] = set()
    for path in sources:
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


def test_every_surface_advertises_the_catalog_version_the_loader_returns():
    """The published catalog and every mirror of its version must agree.

    They did not. #403 bumped `docs/triggers.json` to `0.4` while
    `build_contract_payload()`, `.well-known/agents-shipgate.json`,
    `docs/agent-contract-current.md`, and the rendered local contract all kept
    advertising `0.3` — so an agent that trusted the contract applied the old
    rules to the new catalog, which is the surface external agents actually
    follow (PR #404 review). Nothing compared the two, so nothing failed.
    """

    from agents_shipgate.cli.discovery.agent_instructions.renderers import (
        render_local_contract_file,
    )
    from agents_shipgate.schemas.contract import TRIGGER_CATALOG_SCHEMA_VERSION
    from agents_shipgate.triggers import load_triggers

    published = load_triggers()["schema_version"]

    assert build_contract_payload().trigger_catalog_schema_version == published
    assert TRIGGER_CATALOG_SCHEMA_VERSION == published
    well_known = json.loads(_read(".well-known/agents-shipgate.json"))
    assert well_known["trigger_catalog_schema_version"] == published
    local_contract = json.loads(render_local_contract_file())
    assert local_contract["trigger_catalog_schema_version"] == published
    assert (
        f"Current trigger catalog schema: `{published}`"
        in _read("docs/agent-contract-current.md")
    )


def test_triggers_json_loads_via_canonical_loader():
    """The bundled `agents_shipgate.triggers` module is the canonical
    loader. If a coding agent reads docs/triggers.json directly and
    reaches a different verdict than this loader, that's a drift bug —
    catch it by exercising the loader during CI."""
    triggers = load_triggers()
    assert triggers["schema_version"] == "0.4", (
        "docs/triggers.json schema_version moved off 0.4; bump the "
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
        assert rule.get("surface_class") in VALID_SURFACE_CLASSES, (
            f"rule {rule['id']!r} has missing or unknown surface_class "
            f"{rule.get('surface_class')!r}; allowed: "
            f"{sorted(VALID_SURFACE_CLASSES)}."
        )
        assert rule.get("when"), f"rule {rule['id']!r} missing `when` clause."
        assert rule.get("agents_md_row"), (
            f"rule {rule['id']!r} missing `agents_md_row`; the row text "
            "is what the contract test pins against AGENTS.md prose."
        )


def test_trigger_boundary_adapter_projection_matches_runtime_registry():
    catalog = _load_triggers_json()
    expected = [
        {
            "id": adapter.id,
            "hosts": list(adapter.hosts),
            "exact_paths": list(adapter.exact_paths),
            "globs": list(adapter.globs),
            "experimental": adapter.experimental,
        }
        for adapter in BOUNDARY_ADAPTERS
    ]

    assert catalog["boundary_adapters"] == expected


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
    for host_path in (
        ".claude/settings.json",
        ".cursor/cli.json",
        ".cursor/mcp.json",
        ".vscode/mcp.json",
        ".github/workflows/deploy.yml",
    ):
        host_change = evaluate(paths=[host_path])
        assert host_change["run_shipgate"] is True, (
            f"Host boundary change {host_path!r} must trigger Shipgate; got {host_change!r}."
        )
        assert any(
            match["surface_class"] == "host_boundary" for match in host_change["matched_rules"]
        )

    conductor_change = evaluate(
        paths=["workflows/fulfillment.json"],
        diff_text='+{"type": "CALL_MCP_TOOL"}',
    )
    assert conductor_change["run_shipgate"] is True
    assert any(
        match["surface_class"] == "capability" for match in conductor_change["matched_rules"]
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


@pytest.mark.parametrize(
    "path",
    [
        "src/TEST_agent.py",
        "src/AGENT_TEST.py",
        "TESTS/helper.py",
        "SRC/Tests/support.py",
        "README.MD",
    ],
)
def test_uppercase_production_paths_do_not_read_as_docs_only(path: str):
    """`every_file_matches` must stay case-SENSITIVE.

    It is the docs-only rule's own classifier and `skip_shipgate` beats
    `run_shipgate`, so widening it subtracts evaluation. On a case-sensitive
    filesystem `src/TEST_agent.py` is a production module, not a test: with
    a case-folded matcher it satisfies `**/test_*.py`, and a diff adding
    `@function_tool` beside it is skipped instead of run.

    The sibling predicates are folded precisely because widening them can
    only *add* evaluation — see `test_governance_case_variants_still_run`."""
    result = evaluate(paths=[path], diff_text="+@function_tool\n+def search(): ...")

    assert result["run_shipgate"] is True, (
        f"{path!r} is a production path on a case-sensitive filesystem; a diff "
        f"adding @function_tool beside it must run, not skip. Got {result!r}."
    )
    matched_ids = {m["id"] for m in result["matched_rules"]}
    assert "TRIGGER-DOCS-ONLY-NEGATIVE" not in matched_ids, (
        f"TRIGGER-DOCS-ONLY-NEGATIVE classified {path!r} as docs/tests-only. "
        "`every_file_matches` must not be case-folded."
    )


@pytest.mark.parametrize(
    "paths",
    [
        ["tests/test_agent.py"],
        ["README.md"],
        ["docs/guide.md", "tests/test_b.py"],
    ],
)
def test_lowercase_docs_and_tests_still_skip(paths: list[str]):
    """Negative control for the case-sensitivity split: the docs-only rule
    must keep firing on the genuinely lowercase paths it has always
    covered."""
    result = evaluate(paths=paths, diff_text="+@function_tool\n")
    assert result["run_shipgate"] is False, (
        f"{paths!r} is a real docs/tests-only change set and must still skip; "
        f"got {result!r}."
    )


@pytest.mark.parametrize(
    "path",
    [
        "services/foo/Policies/refund.yaml",
        "enterprise/lib/captain/Prompts/system.md",
        "SHIPGATE.yaml",
        ".github/workflows/Agents-Shipgate.yaml",
    ],
)
def test_governance_case_variants_still_run(path: str):
    """The other half of the split: `glob` and `none_match_glob` are folded,
    because a case variant of a governance path resolves to the canonical
    file on a case-insensitive filesystem and the verifier classifies it as
    a trust root either way."""
    from agents_shipgate.core.trust_roots import trust_root_class_for

    assert trust_root_class_for(path) is not None, (
        f"Fixture drift: {path!r} is no longer a trust root, so it no longer "
        "tests trigger/verifier parity."
    )
    result = evaluate(paths=[path])
    assert result["run_shipgate"] is True, (
        f"{path!r} is classified as a trust root but the catalog routes it as "
        f"{result['skip_reason']!r}."
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


# --- Trust-root surfaces <-> trigger-catalog parity -------------------------
#
# Two lists describe the same governance surfaces from opposite ends.
# `SHIP-VERIFY-POLICY-BASE-ABSENT`'s fail-safe (`_POLICY_SURFACES`) and the
# trust-root graph (`TRUST_ROOT_SURFACES`) classify them recursively; the
# trigger catalog decides whether Shipgate runs on the diff at all. When the
# two disagree, the verifier calls a path a trust root while the catalog
# reports `no_match` — "nothing in this PR signals a tool-surface change" —
# about that same path.
#
# Every governance trust-root pattern below gets a representative path at the
# repo root AND one under a nested workspace, plus a case variant where the
# catalog claims case-insensitive matching. A pattern added to either source
# list without an entry here fails the mapping test, so the lists cannot drift
# apart again unnoticed.
_GOVERNANCE_TRUST_ROOT_CLASSES = frozenset(
    {"manifest", "shipgate_state", "policy", "prompts", "ci_gate"}
)

_TRUST_ROOT_TRIGGER_SAMPLES: dict[str, tuple[str, ...]] = {
    "**/shipgate.yaml": ("shipgate.yaml", "services/foo/shipgate.yaml"),
    "**/.agents-shipgate/**": (
        ".agents-shipgate/baseline.json",
        "services/foo/.agents-shipgate/baseline.json",
    ),
    "**/policies/**": (
        "policies/refund.yaml",
        "services/foo/policies/refund.yaml",
        "services/foo/Policies/refund.yaml",
    ),
    "**/prompts/**": (
        "prompts/system.md",
        "enterprise/lib/captain/prompts/system.md",
        "enterprise/lib/captain/Prompts/system.md",
    ),
    "**/.github/workflows/agents-shipgate.yml": (
        ".github/workflows/agents-shipgate.yml",
        "services/foo/.github/workflows/agents-shipgate.yml",
    ),
    "**/.github/workflows/agents-shipgate.yaml": (
        ".github/workflows/agents-shipgate.yaml",
        "services/foo/.github/workflows/agents-shipgate.yaml",
    ),
}

# Samples the catalog does not route today. The surface is anchored at the
# repo root by the boundary registry's `exact_paths`, which check, verify,
# preflight and audit all share, so widening it is a registry change rather
# than a catalog edit. This set is a tripwire, not an endorsement: close one of
# these gaps and the parity test below tells you to promote it out of the set.
# `services/foo/shipgate.yaml` was promoted out in #363 — a monorepo keeps one
# manifest per project directory, so an edit to the file declaring that
# project's agent, purpose, and tool surface has to route.
_TRUST_ROOT_TRIGGER_GAPS = frozenset(
    {
        "services/foo/.agents-shipgate/baseline.json",
    }
)


def test_governance_trust_root_surfaces_all_have_trigger_parity_samples():
    """Both trust-root lists must be fully represented in
    `_TRUST_ROOT_TRIGGER_SAMPLES`. Adding a governance surface to
    `_POLICY_SURFACES` or `TRUST_ROOT_SURFACES` without deciding how the
    trigger catalog routes it is exactly the drift that let
    `**/policies/**` be a trust root while the catalog matched only
    `policies/**`."""
    from agents_shipgate.checks.verify_policy import _POLICY_SURFACES
    from agents_shipgate.core.trust_roots import TRUST_ROOT_SURFACES

    governance = {
        pattern
        for kind, pattern in TRUST_ROOT_SURFACES
        if kind in _GOVERNANCE_TRUST_ROOT_CLASSES
    }
    missing = (set(_POLICY_SURFACES) | governance) - set(_TRUST_ROOT_TRIGGER_SAMPLES)
    assert not missing, (
        f"Governance trust-root surface(s) {sorted(missing)} have no trigger "
        "parity sample. Add a repo-root and a nested representative path to "
        "_TRUST_ROOT_TRIGGER_SAMPLES and decide which docs/triggers.json rule "
        "routes them."
    )


@pytest.mark.parametrize(
    "pattern,path",
    [
        (pattern, path)
        for pattern, paths in _TRUST_ROOT_TRIGGER_SAMPLES.items()
        for path in paths
    ],
)
def test_trigger_catalog_routes_governance_trust_root_paths(pattern: str, path: str):
    """A path the verifier treats as a governance trust root must not be
    reported as `no_match` by the trigger catalog."""
    result = evaluate(paths=[path])

    if path in _TRUST_ROOT_TRIGGER_GAPS:
        # Read the matched rules, not the verdict. Since #403 an unrouted
        # change set no longer publishes a confident skip — it withholds one —
        # so `run_shipgate is False` would have stopped tripping without the
        # gap being closed, which is the opposite of what a tripwire is for.
        assert not result["matched_rules"], (
            f"{path!r} (trust root {pattern!r}) is now routed by the trigger "
            f"catalog via {[m['id'] for m in result['matched_rules']]}. That "
            "closes a known gap — remove it from _TRUST_ROOT_TRIGGER_GAPS."
        )
        assert result["evaluation_status"] == "unclassified", (
            f"{path!r} is unrouted, so the catalog must withhold its verdict "
            f"rather than publish {result['skip_reason']!r}."
        )
        return

    assert result["run_shipgate"] is True, (
        f"{path!r} matches trust-root surface {pattern!r} but the trigger "
        f"catalog does not route it (skip_reason={result['skip_reason']!r}). "
        "The verifier would call this a trust-root edit while the catalog "
        "says nothing in the PR signals a tool-surface change."
    )


def _collect_globs(pred: object) -> set[str]:
    """Every `glob` leaf in a predicate tree."""
    found: set[str] = set()
    if not isinstance(pred, dict):
        return found
    if isinstance(pred.get("glob"), str):
        found.add(pred["glob"])
    for key in ("any_of", "all_of"):
        for nested in pred.get(key, []):
            found |= _collect_globs(nested)
    return found


def _none_match_globs(pred: object) -> set[str]:
    """Every glob listed in a `none_match_glob` leaf of a predicate tree."""
    found: set[str] = set()
    if not isinstance(pred, dict):
        return found
    globs = pred.get("none_match_glob")
    if isinstance(globs, str):
        found.add(globs)
    elif isinstance(globs, list):
        found |= set(globs)
    for key in ("any_of", "all_of"):
        for nested in pred.get(key, []):
            found |= _none_match_globs(nested)
    return found


def test_governance_globs_are_all_excluded_from_the_docs_only_negative():
    """The docs-only negative rule beats every `run_shipgate` rule, so any
    glob that routes a governance surface must also appear in its
    `none_match_glob` list. Otherwise widening the positive rule changes
    nothing for the file types the negative rule already swallows — a
    `prompts/*.md` edit matches `every_file_matches: **/*.md` and skips.

    Asserted structurally rather than by sample path, because a sample
    whose extension the negative rule never matches (`policies/*.yaml`)
    passes whether or not the exclusion is there."""
    rules = {rule["id"]: rule for rule in _load_triggers_json()["rules"]}
    positive = _collect_globs(rules["TRIGGER-PROMPTS-OR-POLICIES"]["when"])
    excluded = _none_match_globs(rules["TRIGGER-DOCS-ONLY-NEGATIVE"]["when"])

    assert positive, "TRIGGER-PROMPTS-OR-POLICIES has no glob legs to compare."
    missing = positive - excluded
    assert not missing, (
        f"Glob(s) {sorted(missing)} route a governance surface via "
        "TRIGGER-PROMPTS-OR-POLICIES but are absent from "
        "TRIGGER-DOCS-ONLY-NEGATIVE's none_match_glob list. skip_shipgate "
        "beats run_shipgate, so a Markdown file under those paths would "
        "still be classified as docs-only and skipped."
    )


@pytest.mark.parametrize(
    "path",
    [
        "enterprise/lib/captain/prompts/system.md",
        "services/foo/policies/refund.md",
        # Case variants must route identically — the verifier's trust-root
        # classification is case-insensitive, so the catalog's is too.
        "enterprise/lib/captain/Prompts/system.md",
        "services/foo/Policies/refund.md",
    ],
)
def test_nested_prompt_and_policy_edits_beat_the_docs_only_negative(path: str):
    """`TRIGGER-DOCS-ONLY-NEGATIVE` carries the same glob list as
    `TRIGGER-PROMPTS-OR-POLICIES`. If only the positive rule went
    recursive, a nested prompt edit bundled with a docs edit would still
    classify as docs-only and skip — and a nested `prompts/*.md` edit on
    its own would skip via `every_file_matches: **/*.md`.

    Every sample here is Markdown on purpose: that is the extension the
    negative rule actually matches, so these cases fail if the
    `none_match_glob` mirror is dropped."""
    alone = evaluate(paths=[path])
    assert alone["run_shipgate"] is True, (
        f"A lone nested governance edit {path!r} must run; got {alone!r}."
    )

    bundled = evaluate(paths=["README.md", path])
    assert bundled["run_shipgate"] is True, (
        f"{path!r} bundled with a docs edit must still run, not classify as "
        f"docs-only; got {bundled!r}."
    )
    matched_ids = {m["id"] for m in bundled["matched_rules"]}
    assert "TRIGGER-DOCS-ONLY-NEGATIVE" not in matched_ids, (
        "TRIGGER-DOCS-ONLY-NEGATIVE must not fire when a nested prompts/ or "
        f"policies/ path is in the change set; got {bundled!r}."
    )
    assert "TRIGGER-PROMPTS-OR-POLICIES" in matched_ids, (
        f"Expected TRIGGER-PROMPTS-OR-POLICIES to route {path!r}; got "
        f"{bundled!r}."
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


# Reduced from google/adk-python#6605 (`contributing/samples/agent_hooks`):
# two plain functions — one of them destructive — passed straight into an
# `LlmAgent(tools=[...])`. Neither carries a decorator, so the shape has no
# `@function_tool` / `FunctionTool(` token for the decorator rule to see.
_ADK_TOOLS_LIST_DIFF = """\
+from google.adk.agents.llm_agent import LlmAgent
+
+
+def lookup_account(user_id: str) -> dict:
+    return {"user_id": user_id, "api_key": "EXAMPLE_NOT_A_REAL_KEY"}
+
+
+def delete_account(user_id: str) -> dict:
+    return {"user_id": user_id, "status": "deleted"}
+
+
+root_agent = LlmAgent(
+    name="support_agent",
+    tools=[lookup_account, delete_account],
+)
"""

# The ADK quickstart spelling: the `Agent` alias (ADK exports it for
# `LlmAgent`) with the tool list inline. Same capability change, different
# surface syntax.
_ADK_AGENT_ALIAS_DIFF = """\
+from google.adk.agents import Agent
+
+root_agent = Agent(name="weather_agent", tools=[get_weather, refund_order])
"""

# The ordinary case: one tool added to an agent that already exists. `git
# diff` gives three lines of context, so the constructor and the list are in
# the hunk but the import — declared dozens of lines earlier — is not. A rule
# that demands the import in the diff covers only whole-file additions and
# misses every subsequent edit to the same tool list.
_ADK_MODIFIED_TOOLS_LIST_DIFF = """\
@@ -28,7 +28,7 @@ def delete_account(user_id: str) -> dict:
 root_agent = LlmAgent(
     name="support_agent",
     description="A customer-support agent.",
-    tools=[lookup_account],
+    tools=[lookup_account, delete_account],
 )
"""


def test_triggers_google_adk_modified_tools_list_routes_run():
    """Adding a tool to an *existing* `LlmAgent(..., tools=[...])` must route
    the same as adding the agent outright — the AGENTS.md row says
    "Adds/**changes**".

    Regression for the PR #349 review: the first version of this rule
    required a `google.adk` token in the diff, which a modified list does not
    carry, so the common edit returned `no_match`. `LlmAgent(` identifies ADK
    on its own; no other supported framework exports that name."""
    result = evaluate(paths=["agent.py"], diff_text=_ADK_MODIFIED_TOOLS_LIST_DIFF)
    assert result["run_shipgate"] is True, (
        "An edit adding a tool to an existing ADK tools list must route "
        f"run_shipgate; got {result!r}."
    )
    assert "TRIGGER-GOOGLE-ADK-AGENT-TOOLS-CHANGED" in {
        m["id"] for m in result["matched_rules"]
    }, f"Expected the ADK rule to carry the modified-list case; got {result!r}."


def test_triggers_bare_agent_call_without_adk_context_does_not_claim_adk():
    """The `Agent` leg stays gated behind an ADK module token. CrewAI also
    constructs `Agent(..., tools=[...])`, and routing that under a rule ID
    naming Google ADK would be the same defect this catalog is fixing: a rule
    reporting a conclusion its evidence does not support."""
    result = evaluate(
        paths=["crew.py"],
        diff_text="+from crewai import Agent\n+researcher = Agent(role='r', tools=[search])\n",
    )
    assert "TRIGGER-GOOGLE-ADK-AGENT-TOOLS-CHANGED" not in {
        m["id"] for m in result["matched_rules"]
    }, f"A CrewAI `Agent(tools=[...])` must not match the ADK rule; got {result!r}."


def test_triggers_google_adk_agent_tools_list_routes_run():
    """A Google ADK `LlmAgent(..., tools=[...])` addition is a tool-surface
    change and must route positively — not as a framework-upgrade dry-run.

    Regression for #315: the engine already resolves this shape (detection,
    the ADK adapter, and the binding graph all handle it), but the catalog
    carried no ADK rule, so the only thing that fired was a bare `google-adk`
    diff token on the dependency rule and the PR routed `dry_run_only`."""
    result = evaluate(
        paths=["contributing/samples/agent_hooks/agent.py"],
        diff_text=_ADK_TOOLS_LIST_DIFF,
    )
    assert result["should_run"] is True and result["run_shipgate"] is True, (
        f"ADK tools=[...] change must route run_shipgate; got {result!r}."
    )
    matched_ids = {m["id"] for m in result["matched_rules"]}
    assert "TRIGGER-GOOGLE-ADK-AGENT-TOOLS-CHANGED" in matched_ids, (
        "Expected the ADK agent-tools rule to carry the verdict; got "
        f"matched_rules={result['matched_rules']!r}."
    )
    assert "TRIGGER-FRAMEWORK-VERSION-BUMP" not in matched_ids, (
        "The sample mutates no dependency manifest, so the dependency rule "
        f"must not claim a framework version bump; got {result!r}."
    )
    assert result_has_surface_class(result, "capability"), (
        "The ADK rule must be classed `capability` so consumers switching on "
        f"surface_class (undeclared-surface inference) see it; got {result!r}."
    )


def test_triggers_google_adk_agent_alias_routes_run():
    """The quickstart spelling uses the `Agent` alias rather than `LlmAgent`.
    It is the same capability change and must route the same way."""
    result = evaluate(paths=["app/agent.py"], diff_text=_ADK_AGENT_ALIAS_DIFF)
    assert result["run_shipgate"] is True, (
        f"ADK `Agent` alias with an inline tools list must run; got {result!r}."
    )


def test_triggers_do_not_watch_the_spaced_toml_tools_array_token():
    """`tools = [` is TOML array syntax as often as it is Python, and
    `diff_contains` is a substring match — so the token also swallows
    `enabled_tools = [...]` in a Codex config. Watching it would put a token
    with no structural meaning into every `diff_tokens` list, which is the
    reporting defect this catalog is supposed to be fixing."""
    result = evaluate(
        paths=[".codex/config.toml"],
        diff_text='+enabled_tools = ["get_input", "output_summary"]\n',
    )
    assert result["diff_tokens"] == [], (
        "A TOML array must not register as a catalog token; got "
        f"diff_tokens={result['diff_tokens']!r}."
    )
    assert "TRIGGER-GOOGLE-ADK-AGENT-TOOLS-CHANGED" not in {
        m["id"] for m in result["matched_rules"]
    }, f"The ADK rule must not fire on a TOML tools array; got {result!r}."


def test_triggers_google_adk_tool_and_toolset_classes_retain_coverage():
    """ADK's decorator-free tool wrappers were already covered by
    TRIGGER-FUNCTION-TOOL-DECORATOR. Adding the agent-tools rule must not
    move or weaken that coverage."""
    for diff in (
        "+refund = FunctionTool(func=issue_refund)\n",
        "+watcher = LongRunningFunctionTool(func=poll_job)\n",
    ):
        result = evaluate(paths=["agent.py"], diff_text=diff)
        assert result["run_shipgate"] is True, (
            f"ADK tool wrapper {diff!r} must still route run_shipgate; got {result!r}."
        )
        matched_ids = {m["id"] for m in result["matched_rules"]}
        assert "TRIGGER-FUNCTION-TOOL-DECORATOR" in matched_ids, (
            f"Expected the decorator rule to still carry {diff!r}; got {result!r}."
        )


def test_triggers_docs_mentioning_google_adk_is_not_a_framework_version_bump():
    """A bare package token is not evidence that a version moved. A README
    that mentions `google-adk` must not be classified as a framework upgrade
    — the rule would be stating a conclusion its evidence cannot support."""
    result = evaluate(
        paths=["README.md", "docs/quickstart.md"],
        diff_text="+Install the sample with `pip install google-adk`.\n",
    )
    matched_ids = {m["id"] for m in result["matched_rules"]}
    assert "TRIGGER-FRAMEWORK-VERSION-BUMP" not in matched_ids, (
        "A docs-only mention of `google-adk` must not match the dependency "
        f"rule; got matched_rules={result['matched_rules']!r}."
    )
    assert result["dry_run_recommended"] is False, (
        f"Docs-only mention must not recommend a dry run; got {result!r}."
    )
    assert "google-adk" in result["diff_tokens"], (
        "The token is still reported — `diff_tokens` states what is present "
        f"in the diff and draws no conclusion; got {result!r}."
    )


# One representative path per entry in DEPENDENCY_MANIFEST_GLOBS, plus nested
# spellings. `requirements.in` / `constraints.in` and the modern lockfiles are
# here because the PR #349 review caught them dropping out of advisory
# coverage: narrowing the rule to a hand-written allowlist silently regressed
# every pip-tools repo, which authors bumps in `.in` and compiles to `.txt`.
_DEPENDENCY_MANIFEST_SAMPLE_PATHS = (
    "pyproject.toml",
    "services/api/pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.in",
    "requirements-dev.in",
    "requirements/base.txt",
    "requirements/base.in",
    "constraints.txt",
    "constraints.in",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "uv.lock",
    "pdm.lock",
    "pylock.toml",
    "pylock.dev.toml",
    "environment.yml",
    "environment.yaml",
    "conda-lock.yml",
    "conda-lock.yaml",
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "gradle/libs.versions.toml",
)


def test_triggers_framework_version_bump_requires_a_dependency_manifest():
    """The dependency rule must observe both halves of its claim: a
    framework package token AND a changed dependency manifest. A source-only
    refactor that merely imports the framework is not a version bump."""
    source_only = evaluate(
        paths=["src/agent.py"],
        diff_text="+from langchain_core.tools import BaseTool\n",
    )
    assert "TRIGGER-FRAMEWORK-VERSION-BUMP" not in {
        m["id"] for m in source_only["matched_rules"]
    }, f"Token without a dependency manifest must not match; got {source_only!r}."

    for manifest in _DEPENDENCY_MANIFEST_SAMPLE_PATHS:
        bumped = evaluate(
            paths=[manifest],
            diff_text='-"langchain==0.2.0"\n+"langchain==0.3.0"\n',
        )
        assert bumped["dry_run_recommended"] is True, (
            f"A real bump in {manifest!r} must still recommend a dry run; "
            f"got {bumped!r}."
        )


def test_dependency_manifest_projection_matches_runtime_set():
    """`TRIGGER-FRAMEWORK-VERSION-BUMP`'s path leg is a projection of
    `DEPENDENCY_MANIFEST_GLOBS`, the one place that answers "is this a file
    where a dependency version is declared or locked?". Pin the projection so
    the catalog cannot drift from the runtime set — the same guard
    `boundary_adapters` already gets."""
    triggers = _load_triggers_json()
    rule = next(
        r for r in triggers["rules"] if r["id"] == "TRIGGER-FRAMEWORK-VERSION-BUMP"
    )
    path_leg = next(
        leg
        for leg in rule["when"]["all_of"]
        if any("glob" in nested for nested in leg.get("any_of", []))
    )
    projected = [nested["glob"] for nested in path_leg["any_of"]]
    assert projected == list(DEPENDENCY_MANIFEST_GLOBS), (
        "docs/triggers.json's dependency-manifest globs drifted from "
        "agents_shipgate.core.dependency_manifests.DEPENDENCY_MANIFEST_GLOBS. "
        "Update the catalog in the same commit as the constant."
    )


@pytest.mark.parametrize("path", _DEPENDENCY_MANIFEST_SAMPLE_PATHS)
def test_dependency_manifest_samples_are_recognized_by_both(path):
    """Every sample path must be recognized by the runtime helper AND route
    through the catalog rule. Catches a glob that reads plausibly but matches
    nothing — a silent hole in exactly the direction the review found."""
    assert is_dependency_manifest(path), (
        f"{path!r} is a dependency manifest but DEPENDENCY_MANIFEST_GLOBS "
        "does not match it."
    )
    result = evaluate(paths=[path], diff_text="-crewai==0.1.0\n+crewai==0.2.0\n")
    assert result["dry_run_recommended"] is True, (
        f"A framework bump in {path!r} must recommend a dry run; got {result!r}."
    )


@pytest.mark.parametrize(
    "path",
    ["README.md", "docs/install.md", "Dockerfile", "src/agent.py", "notes.txt"],
)
def test_non_manifest_paths_are_not_dependency_manifests(path):
    """The negative control. A file that merely *mentions* a package cannot
    support the claim that a dependency changed, so it must stay outside the
    set however plausibly a bump might be written in it."""
    assert not is_dependency_manifest(path), (
        f"{path!r} must not count as a dependency manifest."
    )


def test_framework_version_bump_rule_cannot_fire_on_a_token_alone():
    """Structural pin for the rationale-honesty fix: the dependency rule's
    `when` clause must keep a path leg alongside the token leg. Reverting it
    to a bare `any_of` of `diff_contains` tokens would restore the defect
    where a bare token is reported as a framework upgrade."""
    triggers = _load_triggers_json()
    rule = next(
        (r for r in triggers["rules"] if r["id"] == "TRIGGER-FRAMEWORK-VERSION-BUMP"),
        None,
    )
    assert rule is not None, "TRIGGER-FRAMEWORK-VERSION-BUMP must remain in the catalog."
    legs = rule["when"].get("all_of")
    assert legs, (
        "TRIGGER-FRAMEWORK-VERSION-BUMP must conjoin its token leg with a "
        f"dependency-manifest path leg; got when={rule['when']!r}."
    )
    assert any(
        "glob" in nested for leg in legs for nested in leg.get("any_of", [leg])
    ), (
        "TRIGGER-FRAMEWORK-VERSION-BUMP lost its dependency-manifest path "
        f"leg; got when={rule['when']!r}."
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
    assert feedback.get("issue_template", "").endswith("/issues/new?template=agent_feedback.yml")
    assert "shipgate-feedback.json" in feedback.get("attach", [])
    forbidden = set(feedback.get("do_not_attach", []))
    assert {"unredacted reports", "raw tool outputs", "secrets", "chain-of-thought"} <= (forbidden)


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
        "agents-shipgate init --workspace . --write --json"
    )
    assert data.get("check_run_policies") == [
        "advisory",
        "blocked-fails",
        "require-mergeable",
    ]
    assert (
        data.get("github_action_pr_workflow", {}).get("recommended_inputs", {}).get("diff_base")
        == "target"
    )
    assert "feedback export" in commands.get("feedback_export", "")
    assert data.get("fixture_run") == "agents-shipgate fixture run ai_generated_refund_pr"
    assert data.get("static_scan_fixture_run") == (
        "agents-shipgate fixture run support_refund_agent"
    )
    assert data.get("verifier_read_order", [])[:10] == [
        "control.state",
        "authorization",
        "execution",
        "merge_verdict",
        "applicability",
        "can_merge_without_human",
        "control.next_action",
        "fix_task",
        "capability_review.top_changes",
        "release_decision.decision",
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
    slash_commands = slash.split("Prominent commands:", 1)[1].split("Required behavior", 1)[0]
    target_snippets = _read("docs/target-repo-agent-snippets.md")
    agents_block = target_snippets.split("## `AGENTS.md`", 1)[1].split("## Codex Skill", 1)[0]
    claude_block = target_snippets.split("## `CLAUDE.md`", 1)[1].split(
        "## `.cursor/rules/agents-shipgate.mdc`", 1
    )[0]
    cursor_block = target_snippets.split("## `.cursor/rules/agents-shipgate.mdc`", 1)[1].split(
        "## `.github/pull_request_template.md`", 1
    )[0]

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
# Excludes triggers the hook regex cannot cover: diff-only ones (decorator,
# ADK agent tools), file_present-only ones (existing manifest), and the
# dependency rule — its path leg names dependency manifests, but it only
# fires when a framework token is in the diff body too, so a path-only
# regex cannot decide it.
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
        ".codex/hooks.json",
        ".codex/requirements.toml",
        "sub/.codex/config.toml",
    ],
    "TRIGGER-CLAUDE-BOUNDARY-CONFIG-CHANGED": [
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".mcp.json",
        ".claude/commands/review.md",
        ".claude/commands",
        "claude.md",
    ],
    "TRIGGER-CURSOR-BOUNDARY-CONFIG-CHANGED": [
        ".cursor/cli.json",
        ".cursor/mcp.json",
        ".cursor/rules/security.mdc",
        ".cursor/rules",
    ],
    "TRIGGER-VSCODE-MCP-BOUNDARY-CHANGED": [
        ".vscode/mcp.json",
    ],
    "TRIGGER-SHARED-HOST-BOUNDARY-CHANGED": [
        "AGENTS.md",
        "AGENTS.override.md",
        "CLAUDE.md",
        ".agents/skills/shipgate/SKILL.md",
        ".claude/skills/shipgate/SKILL.md",
        ".github/workflows/release.yml",
        "sub/.github/workflows/release.yml",
        ".shipgate/agent-contract.json",
    ],
    "TRIGGER-N8N-WORKFLOW-CHANGED": [
        "workflows/my-n8n-export.json",
        "packages/flows/.n8n/credentials.json",
        ".n8n",
    ],
    "TRIGGER-CONDUCTOR-WORKFLOW-CHANGED": [
        "conductor/fulfillment.json",
        "services/orders/conductor/workflows/refund.json",
        "ai/examples/agent_task.json",
    ],
    "TRIGGER-PROMPTS-OR-POLICIES": [
        "prompts/system.md",
        "policies/refund.md",
        "enterprise/lib/captain/prompts/system.md",
        "services/foo/policies/refund.yaml",
        # Case variants: Git can carry a spelling that resolves to the
        # canonical governance directory on a case-insensitive filesystem.
        "services/foo/Policies/refund.yaml",
        "enterprise/lib/captain/Prompts/system.md",
        # `dir/**` matches the path itself, so a tracked file or symlink
        # named exactly `prompts`/`policies` is in scope too.
        "prompts",
        "services/foo/policies",
    ],
    "TRIGGER-SHIPGATE-MANIFEST": [
        "shipgate.yaml",
        # A monorepo keeps one manifest per project directory; an edit to
        # the file that declares that project's agent, purpose, and tool
        # surface has to route like a root-level one (#363).
        "services/refund/shipgate.yaml",
    ],
    "TRIGGER-SHIPGATE-CI-WORKFLOW": [
        ".github/workflows/agents-shipgate.yml",
        ".github/workflows/agents-shipgate.yaml",
    ],
}

# Catalog rules with a `glob` leg that the hook's `files:` regex
# deliberately does not decide. Anything else must have fixtures above.
_HOOK_PATH_TRIGGERS_EXCLUDED = {
    # The path leg names dependency manifests, but the rule only fires when
    # a framework token is in the diff body too. A path-only regex staging
    # every `package.json` edit would be a different, much broader hook.
    "TRIGGER-FRAMEWORK-VERSION-BUMP",
    # A negative rule. Its globs describe what must NOT be staged.
    "TRIGGER-DOCS-ONLY-NEGATIVE",
}


def _rule_has_glob_leg(pred: object) -> bool:
    if not isinstance(pred, dict):
        return False
    if "glob" in pred or "every_file_matches" in pred:
        return True
    return any(
        _rule_has_glob_leg(nested)
        for key in ("any_of", "all_of")
        if key in pred
        for nested in pred[key]
    )


def test_hook_regex_fixtures_cover_every_path_based_trigger():
    """`_HOOK_PATH_TRIGGER_FIXTURES` must be exhaustive over the catalog.

    The hook manifest and both copy-paste snippets claim the regex covers
    every trigger a path alone can decide. That claim was false while the
    fixture table silently omitted the n8n and Conductor rules: nothing
    failed, because the table only tested what it already listed. Every
    catalog rule carrying a glob leg now has to be listed here or named in
    `_HOOK_PATH_TRIGGERS_EXCLUDED` with a reason.
    """
    triggers = _load_triggers_json()
    path_based = {
        rule["id"] for rule in triggers["rules"] if _rule_has_glob_leg(rule.get("when"))
    }
    unclassified = path_based - set(_HOOK_PATH_TRIGGER_FIXTURES) - _HOOK_PATH_TRIGGERS_EXCLUDED
    assert not unclassified, (
        f"docs/triggers.json rule(s) {sorted(unclassified)} have a path leg but no "
        "hook fixture. Add representative paths to _HOOK_PATH_TRIGGER_FIXTURES (and "
        "a `files:` clause to .pre-commit-hooks.yaml), or name the rule in "
        "_HOOK_PATH_TRIGGERS_EXCLUDED with the reason a path cannot decide it."
    )
    stale = set(_HOOK_PATH_TRIGGERS_EXCLUDED) - {rule["id"] for rule in triggers["rules"]}
    assert not stale, (
        f"_HOOK_PATH_TRIGGERS_EXCLUDED names rule(s) {sorted(stale)} that no longer "
        "exist in docs/triggers.json."
    )


def _representative_paths(pattern: str) -> set[str]:
    """Concrete paths a glob is meant to match, at every globstar arity.

    `**` matches zero or more path segments, so a generator that expands each
    one to a single fixed directory is not a sweep — it is one sample. Each
    `**` segment here independently takes 0, 1 or 2 segments and the cartesian
    product is emitted, which produces the three witnesses a naive expansion
    drops:

    - **Zero segments at a leading `**`.** `**/.app.json` must yield repo-root
      `.app.json`. Without it, narrowing a hook clause from `.*\\.app\\.json`
      to `.+/\\.app\\.json` passes while dropping every root-level match.
    - **Zero segments at an *internal* `**`.** `**/conductor/**/*.json` must
      yield `service/conductor/job.json`, not only `.../conductor/<dir>/...`.
    - **Bare directory.** `dir/**` at arity 0 is `dir` itself, which this
      project's globstar matches. `dir/*` correctly gets no such form — that
      glob requires a segment after the slash.

    Every path is checked against its own source glob by
    `test_representative_paths_are_matched_by_their_source_glob`, so a
    synthesis bug cannot quietly emit paths that pass the sweep by never
    being run-worthy in the first place.
    """
    fillers = ("alpha", "beta")
    arities = (0, 1, 2)

    def literal(segment: str) -> str:
        return segment.replace("*", "item").replace("?", "x")

    # Every `**` segment independently takes 0, 1 or 2 path segments, and the
    # cartesian product is generated. A single fixed expansion per globstar is
    # what let `**/conductor/**/*.json` produce `conductor/nested/item.json`
    # but never `service/conductor/job.json`, so a narrowed Conductor clause
    # stayed green.
    results: set[str] = {""}
    for segment in pattern.replace("\\", "/").split("/"):
        if segment == "**":
            results = {
                "/".join(filter(None, (prefix, *fillers[:arity])))
                for prefix in results
                for arity in arities
            }
        else:
            results = {"/".join(filter(None, (prefix, literal(segment)))) for prefix in results}
    return {path for path in results if path and not path.endswith("/")}


# Both hooks gate on the same trigger surface; they differ only in
# `--ci-mode`. `agents-shipgate-validate` is excluded on purpose — it is the
# manifest doctor and is meant to be `^shipgate\.yaml$` and nothing else.
_GATING_HOOK_IDS = ("agents-shipgate", "agents-shipgate-strict")


def _hook_regex_source_patterns() -> set[str]:
    """Every positive catalog glob plus every boundary-adapter path."""
    from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS

    patterns: set[str] = set()
    for rule in _load_triggers_json()["rules"]:
        if rule["id"] in _HOOK_PATH_TRIGGERS_EXCLUDED:
            continue
        patterns |= _collect_globs(rule.get("when"))
    for adapter in BOUNDARY_ADAPTERS:
        patterns |= set(adapter.globs) | set(adapter.exact_paths)
    return patterns


def test_representative_paths_are_matched_by_their_source_glob():
    """Self-check on the sweep's synthesis.

    A generated corpus is only as good as its generator: a path that does
    not actually match the glob it was derived from would make the parity
    sweep pass for the wrong reason. Assert the corpus is well-formed before
    trusting a green sweep."""
    from agents_shipgate.core.globbing import glob_match

    bad = [
        (source, path)
        for source in sorted(_hook_regex_source_patterns())
        for path in sorted(_representative_paths(source))
        if not glob_match(source, path)
    ]
    assert not bad, (
        "_representative_paths synthesized paths that their own source glob "
        "does not match, so the parity sweep would be testing nothing:\n"
        + "\n".join(f"  {source} -> {path}" for source, path in bad)
    )


@pytest.mark.parametrize("hook_id", _GATING_HOOK_IDS)
def test_hook_regex_stages_every_path_the_evaluator_would_run_on(hook_id: str):
    """Generated sweep: the hook must not be narrower than the evaluator.

    The hand-written fixture table above only checks paths someone thought
    to write down, which is how the bare `.claude/commands` and
    `.cursor/rules` forms stayed uncovered while the manifest claimed
    `dir/**` parity. This derives representative paths from every positive
    catalog glob and every boundary-adapter path instead, so a clause that
    is narrower than its glob fails here without anyone predicting it.
    """
    from agents_shipgate.triggers import evaluate as evaluate_triggers

    pattern = _hook_files_regex(hook_id)
    patterns = _hook_regex_source_patterns()
    assert len(patterns) > 40, f"Only swept {len(patterns)} patterns; synthesis broke."

    # Pass the catalog explicitly: `evaluate` re-reads and re-parses
    # docs/triggers.json on every call otherwise, and the arity sweep makes
    # hundreds of calls.
    catalog = _load_triggers_json()
    missed = [
        (source, path)
        for source in sorted(patterns)
        for path in sorted(_representative_paths(source))
        if evaluate_triggers(paths=[path], triggers=catalog)["run_shipgate"]
        and not pattern.match(path)
    ]
    assert not missed, (
        "The pre-commit `files:` regex is narrower than the trigger catalog for:\n"
        + "\n".join(f"  {source} -> {path}" for source, path in missed)
        + "\nThe hook would not stage a change the evaluator says must run. Widen "
        "the clause in .pre-commit-hooks.yaml (both hook ids) and re-sync the two "
        "documented snippets."
    )


def _hook_files_regex(hook_id: str = "agents-shipgate") -> re.Pattern[str]:
    """Extract a gating hook's `files:` regex from the root
    .pre-commit-hooks.yaml so the test parses the same pattern pre-commit
    will at install time."""
    import yaml

    text = _read(".pre-commit-hooks.yaml")
    hooks = yaml.safe_load(text)
    hook = next(h for h in hooks if h["id"] == hook_id)
    pattern = hook["files"]
    # pre-commit compiles with re.VERBOSE since the manifest uses `|`
    # block scalars with comments and whitespace.
    return re.compile(pattern, re.VERBOSE)


# Directories holding shipped, copy-pasteable CI recipes, per provider.
_CI_RECIPE_DIRS = ("github-actions", "gitlab-ci", "circleci")

# Provider-specific ways to say "only run this job when these paths changed".
# Every one of them is an allowlist evaluated against changed paths, and every
# one matches case-sensitively, so none can express what the trigger catalog
# routes. Values are (key, human-readable location) probes applied to any
# mapping in the document.
_CHANGE_PREFILTER_KEYS = (
    "paths",  # GitHub Actions on.<event>.paths
    "paths-ignore",  # GitHub Actions on.<event>.paths-ignore
    "changes",  # GitLab CI rules[].changes / only.changes
)

# CircleCI has no declarative form, so recipes express it as a shell diff-gate
# that exits before the scan. Detected textually.
_CHANGE_PREFILTER_SHELL_MARKERS = ("git diff --name-only",)


def _yaml_mappings(node: object):
    """Every mapping in a parsed YAML document, depth-first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _yaml_mappings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _yaml_mappings(item)


def test_shipped_ci_recipes_do_not_prefilter_on_changed_paths():
    """No shipped CI recipe, on any provider, may gate Shipgate behind a
    changed-path allowlist.

    Two independent reasons, and each alone is disqualifying:

    1. `TRIGGER-EXISTING-MANIFEST-PRESENT` is `force_run`, so a repo with a
       `shipgate.yaml` is contracted to run on every PR. A prefilter does not
       save the scan it claims to save; it silently opts the repo out of its
       own gate.
    2. Every prefilter language here — GitHub `paths`/`paths-ignore`, GitLab
       `changes`, a CircleCI shell diff-gate — matches case-sensitively, while
       the trigger catalog matches governance paths case-insensitively on
       purpose. `- 'policies/**'` therefore drops
       `services/foo/Policies/refund.yaml`, a policy trust root, with no job,
       no check and no signal at all.

    Covers `.yml` and `.yaml`, since a recipe added under the other extension
    would otherwise skip this guard entirely.
    """
    import yaml

    recipes = sorted(
        path
        for directory in _CI_RECIPE_DIRS
        for suffix in ("*.yml", "*.yaml")
        for path in (REPO_ROOT / "examples" / directory).glob(suffix)
    )
    assert len(recipes) > 10, (
        f"Only found {len(recipes)} CI recipes; the directories or globs moved "
        "and this guard would pass vacuously."
    )

    offenders: list[str] = []
    for path in recipes:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT)
        document = yaml.safe_load(text)
        for mapping in _yaml_mappings(document):
            for key in _CHANGE_PREFILTER_KEYS:
                # GitHub Actions `jobs.<id>.steps[].uses`-style keys never
                # collide with these; `artifacts.paths` (GitLab) is an output
                # path list, not a filter, so scope `paths` to filter context.
                if key not in mapping:
                    continue
                if key == "paths" and not _is_change_filter_context(mapping):
                    continue
                offenders.append(f"{relative}: {key}")
        for marker in _CHANGE_PREFILTER_SHELL_MARKERS:
            if marker in text:
                offenders.append(f"{relative}: shell diff-gate ({marker!r})")

    assert not offenders, (
        "CI recipe(s) gate Shipgate behind a changed-path prefilter: "
        f"{sorted(set(offenders))}. An adopted repo is contracted to run on "
        "every PR (force_run), and no prefilter language can express the "
        "catalog's case-insensitive governance matching — so the filter only "
        "drops changes the gate most needs to see. Remove it and let the "
        "in-job trigger evaluator decide."
    )


def _is_change_filter_context(mapping: dict) -> bool:
    """Whether a `paths` key is a changed-path filter rather than an output
    path list.

    GitLab's `artifacts.paths` and CircleCI's `store_artifacts.path` name
    outputs to retain; only GitHub Actions event filters and GitLab
    `rules[].changes.paths` gate on what changed. Filters never carry the
    artifact keys.
    """
    return not ({"when", "expire_in", "reports"} & set(mapping))


def _hook_selects(hook: dict, path: str, tags: set[str]) -> bool:
    """Model pre-commit's file selection for one hook.

    pre-commit applies, in order: the `files` regex (and `exclude`), then
    `tags >= types` (AND), then `tags & types_or` (OR, when set), then
    `tags & exclude_types` must be empty. `types` defaults to `["file"]`,
    which is exactly the default that silently drops symlinks — testing the
    compiled `files` regex alone cannot see that.
    """
    if not re.compile(hook["files"], re.VERBOSE).match(path):
        return False
    exclude = hook.get("exclude")
    if exclude and re.compile(exclude, re.VERBOSE).match(path):
        return False
    if not set(hook.get("types", ["file"])) <= tags:
        return False
    types_or = set(hook.get("types_or", []))
    if types_or and not types_or & tags:
        return False
    return not set(hook.get("exclude_types", [])) & tags


# identify's tags for the two shapes a tracked Git entry can take. A symlink
# is tagged `symlink` and is NOT tagged `file`.
_REGULAR_FILE_TAGS = {"file", "text"}
_SYMLINK_TAGS = {"symlink"}


@pytest.mark.parametrize("hook_id", _GATING_HOOK_IDS)
@pytest.mark.parametrize(
    "path",
    ["prompts", "policies", "services/foo/policies", "AGENTS.md", ".cursor/rules"],
)
def test_gating_hooks_select_governance_symlinks(hook_id: str, path: str):
    """A governance path that is a tracked symlink must still invoke the hook.

    `files:` is not the whole filter. pre-commit's default `types: [file]` is
    an AND-filter applied first, and a tracked symlink carries the `symlink`
    tag rather than `file` — so a symlinked `prompts` directory was dropped
    before the regex ran, despite the regex matching it and the catalog
    routing it. This asserts effective selection, not just the regex.
    """
    import yaml

    hook = next(
        h for h in yaml.safe_load(_read(".pre-commit-hooks.yaml")) if h["id"] == hook_id
    )
    assert _hook_selects(hook, path, _REGULAR_FILE_TAGS), (
        f"{hook_id} does not select regular file {path!r}; the regex or the "
        "type filter regressed."
    )
    assert _hook_selects(hook, path, _SYMLINK_TAGS), (
        f"{hook_id} does not select {path!r} when it is a tracked symlink. "
        "pre-commit applies `types` (default `[file]`) before `files:`, and a "
        "symlink is tagged `symlink`, not `file`. Set `types: []` with "
        "`types_or: [file, symlink]` on the hook."
    )


@pytest.mark.parametrize("hook_id", _GATING_HOOK_IDS)
def test_gating_hooks_still_reject_non_trigger_paths_for_both_tag_shapes(hook_id: str):
    """Negative control for the widened type filter: accepting the `symlink`
    tag must not turn the hook into `always_run`."""
    import yaml

    hook = next(
        h for h in yaml.safe_load(_read(".pre-commit-hooks.yaml")) if h["id"] == hook_id
    )
    for path in ("README.md", "src/agents_shipgate/cli/main.py", "myprompts/foo.md"):
        for tags in (_REGULAR_FILE_TAGS, _SYMLINK_TAGS):
            assert not _hook_selects(hook, path, tags), (
                f"{hook_id} selects {path!r} (tags={sorted(tags)}); it is not a "
                "trigger surface."
            )


def test_both_gating_hooks_share_one_files_expression():
    """Advisory and strict must gate on the same paths.

    They differ only in `--ci-mode`, so a clause added to one and not the
    other means a repo on the strict hook silently gets a narrower gate than
    the advisory one it was told is equivalent. Every parity check below is
    parameterized over both ids; this test is what makes it impossible to
    satisfy them by editing only the advisory entry.
    """
    import yaml

    hooks = yaml.safe_load(_read(".pre-commit-hooks.yaml"))
    expressions = {
        hook_id: next(h for h in hooks if h["id"] == hook_id)["files"]
        for hook_id in _GATING_HOOK_IDS
    }
    advisory, strict = (expressions[hook_id] for hook_id in _GATING_HOOK_IDS)
    assert advisory == strict, (
        "`agents-shipgate` and `agents-shipgate-strict` have different "
        "`files:` expressions. They gate on the same trigger surface and "
        "differ only in --ci-mode; apply the clause to both."
    )


@pytest.mark.parametrize("hook_id", _GATING_HOOK_IDS)
def test_pre_commit_hook_regex_covers_every_path_based_trigger(hook_id: str):
    """The hook docs (README, integrations.md, hook file header) claim
    the `files:` regex covers every path-based trigger in
    docs/triggers.json. Pin that claim: each representative path for
    each path-based trigger ID MUST match the regex. If this fails, a
    new path-based trigger landed in the catalog without a
    corresponding regex update."""
    pattern = _hook_files_regex(hook_id)
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


@pytest.mark.parametrize("hook_id", _GATING_HOOK_IDS)
def test_pre_commit_hook_regex_skips_docs_only_paths(hook_id: str):
    """Negative control: the hook must NOT fire on pure docs / tests /
    config files that aren't tool-surface artifacts. Mirrors the
    `TRIGGER-DOCS-ONLY-NEGATIVE` rule."""
    pattern = _hook_files_regex(hook_id)
    docs_only_paths = [
        "README.md",
        "docs/index.md",
        "tests/test_foo.py",
        "src/agents_shipgate/cli/main.py",
        "docs/release-notes.yml",
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
    # Advisory workflows fail only on blocked/unknown: human_review_required
    # is decided by the PR reviewer and no verifier mechanism can clear it,
    # so failing CI on it would leave trust-root-touching PRs permanently red.
    assert 'fail_on_merge_verdicts: "blocked,unknown"' in workflow


@pytest.mark.parametrize(
    "doc", ["docs/integrations.md", "examples/pre-commit/README.md"]
)
def test_pre_commit_local_docs_show_same_path_trigger_clauses(doc: str):
    """The copy-paste snippets must not lag the root hook.

    Downstream users often copy a documented snippet directly instead of
    using the canonical `repo: https://...` install form, so a snippet that
    lags is a silently narrower gate than the one the surrounding prose
    promises. Every clause is derived from `.pre-commit-hooks.yaml` rather
    than hardcoded, so adding a clause to the canonical hook without
    updating the docs fails here.
    """
    import yaml

    hooks = yaml.safe_load(_read(".pre-commit-hooks.yaml"))
    canonical = next(h for h in hooks if h["id"] == "agents-shipgate")["files"]
    clauses = [line.strip() for line in canonical.splitlines()][1:-1]
    assert clauses, "Could not read any clause from the canonical hook regex."

    text = _read(doc)
    for clause in clauses:
        assert clause in text, (
            f"{doc} pre-commit snippet is missing {clause!r}; keep it aligned "
            "with the root .pre-commit-hooks.yaml regex."
        )
    assert "(?ix)^(" in text, (
        f"{doc} pre-commit snippet must use the case-insensitive `(?ix)` form: "
        "the trigger catalog matches paths case-insensitively, so a "
        "case-sensitive snippet misses `Policies/` on a case-insensitive "
        "filesystem."
    )
    for declaration in ("types: []", "types_or: [file, symlink]"):
        assert declaration in text, (
            f"{doc} pre-commit snippet is missing `{declaration}`. pre-commit's "
            "default `types: [file]` is applied before `files:` and drops a "
            "tracked symlink, so a symlinked governance path would invoke "
            "neither hook."
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

READ_FIRST_PATTERN = re.compile(r"(?:[Rr]ead|[Vv]alidate)\s+`([^`]+)`\s+first")
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
    artifact in the runtime contract's agent_read_order
    (current-control.json since contract v20; verification-receipt.json in
    v17-v19), optionally with a
    reports-dir prefix or a field path suffix. README shipped contradictory
    first-artifact instructions before this invariant; this
    pins the prose surfaces to the contract so the contradiction cannot
    return. verifier.json stays the authoritative controller substrate —
    mentioning it is fine, telling an agent to read it *first* is not."""
    contract = build_contract_payload().model_dump(mode="json")
    first_artifact = contract["agent_read_order"][0]
    assert first_artifact == "current-control.json", (
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


@pytest.mark.parametrize("relpath", RENDERED_PROMPT_PIN_FILES)
def test_rendered_prompt_pins_match_the_emitting_build(relpath):
    """A rendered prompt's runner pin must satisfy the floor it demands.

    Both come from ``{{ shipgate_version }}`` and
    ``{{ minimum_control_contract_version }}``, rendered by the build that
    emitted the file. Hand-writing either is what produced the contradiction
    these prompts shipped with: a pinned runner reporting contract 10 beside a
    demand for a much higher floor, which no agent following the prompt
    literally could ever satisfy.
    """

    for pattern in (PIP_PIN_PATTERN, UVX_PIN_PATTERN):
        for line_number, line, found in _file_lines_with_pin(relpath, pattern):
            assert found == __version__, (
                f"{relpath}:{line_number} pins agents-shipgate {found}; this "
                f"build is {__version__}. Render the pin from "
                f"{{{{ shipgate_version }}}} rather than hand-writing it.\n"
                f"  line: {line.strip()!r}"
            )


def test_discovery_and_runtime_publish_the_same_compatibility_floor():
    """`.well-known` is the authoritative discovery payload; it must not lag.

    An agent that reads it to decide whether the installed CLI is new enough
    gets the wrong answer whenever these two disagree — and they did: the file
    advertised `contract_version: "23"` after the runtime moved to 24, so a
    consumer could accept a CLI the contract no longer describes. Pinned here
    rather than left to the bump checklist, because the checklist is what missed
    it.
    """

    from agents_shipgate.schemas.contract import (
        CONTRACT_VERSION,
        MINIMUM_CONTROL_CONTRACT_VERSION,
    )

    published = json.loads(_read(".well-known/agents-shipgate.json"))

    assert published["contract_version"] == CONTRACT_VERSION
    assert published["minimum_control_contract_version"] == MINIMUM_CONTROL_CONTRACT_VERSION

    # ...and the canonical contract page, which said 24 while the runtime said
    # 21, so a consumer following it rejected the very build it documents.
    stamp = (
        f"- Runtime contract: `{CONTRACT_VERSION}` "
        f"(minimum control contract: `{MINIMUM_CONTROL_CONTRACT_VERSION}`)"
    )
    assert stamp in _read("docs/agent-contract-current.md")

    # ...and the two prose copies of the same floor. Both are read as
    # prerequisites — STABILITY.md documents the field, README.md tells an
    # adopter what to check `contract --json` against — so a consumer following
    # either accepted a pre-v21 control reader while the runtime required 21.
    # Every *current* statement of the floor is asserted; the historical
    # migration notes keep the version they shipped with, which is the point of
    # a migration note.
    assert (
        "`AgentControl` state is authoritative; currently "
        f'`"{MINIMUM_CONTROL_CONTRACT_VERSION}"`' in _read("STABILITY.md")
    )
    assert (
        "the permission-scoped agent-control model requires "
        f'`minimum_control_contract_version: "{MINIMUM_CONTROL_CONTRACT_VERSION}"`'
        in _read("README.md")
    )


def test_the_control_union_stays_out_of_the_durable_schemas_it_is_embedded_in():
    """Six published schemas embed `AgentControl`; widening it widens all six.

    `verifier`, `agent-handoff`, `preflight`, `agent-result`,
    `agent-boundary-result`, and `verify-run` all carry a control block, and
    five of them record no `contract_version` — so a consumer holding a stored
    payload cannot use the runtime floor to work out which shape it has. A
    variant added to the union for one new producer therefore changes six
    durable contracts under unchanged identifiers.

    This pins the action union itself, which is what a widening would have to go
    through, so the next attempt fails here rather than in a consumer's parser.
    """

    from agents_shipgate.schemas.agent_control import AgentControlAction, CodingAgentAction

    def kinds(alias: object) -> set[str]:
        found: set[str] = set()
        for variant in alias.__value__.__origin__.__args__:  # type: ignore[attr-defined]
            annotation = variant.model_fields["kind"].annotation
            found.update(getattr(annotation, "__args__", (annotation,)))
        return found

    assert kinds(CodingAgentAction) == {
        "verify",
        "discover",
        "configure",
        "initialize",
        "repair",
        "install",
        "rerun",
        "fetch_base",
    }
    assert kinds(AgentControlAction) == kinds(CodingAgentAction) | {"review", "stop"}
