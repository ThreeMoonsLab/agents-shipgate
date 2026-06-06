from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agents_shipgate.cli.capability import _build_lock_from_config
from agents_shipgate.cli.verify import run_verify
from agents_shipgate.core.capability_lock import diff_capability_locks
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.schemas.capabilities import (
    CapabilityFactV1,
    CapabilityLockChangedFact,
    CapabilityLockDiffV1,
)
from agents_shipgate.schemas.governance_benchmark import (
    CapabilityExpectationV1,
    GovernanceBenchmarkCatalogV1,
    GovernanceBenchmarkMetricSummaryV1,
    GovernanceBenchmarkResultV1,
    GovernanceBenchmarkSummaryV1,
    GovernanceCapabilityDiffActualV1,
    GovernanceCaseResultV1,
    GovernanceCaseStatus,
    GovernanceGateActualV1,
    GovernanceMetricName,
    GovernanceMetricResultV1,
)
from agents_shipgate.schemas.verifier import VerifierArtifact


@dataclass(frozen=True)
class GovernanceBenchmarkOptions:
    case_ids: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    include_catalog_only: bool = False
    strict: bool = False


@dataclass(frozen=True)
class _FixtureTrees:
    base_files: dict[str, str | None]
    head_files: dict[str, str | None]


def load_governance_benchmark_catalog(path: Path) -> GovernanceBenchmarkCatalogV1:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputParseError(f"Governance benchmark catalog not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise InputParseError(f"Invalid governance benchmark catalog YAML: {path}: {exc}") from exc
    try:
        return GovernanceBenchmarkCatalogV1.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid governance benchmark catalog {path}: {exc}") from exc


def render_governance_benchmark_result_json(result: GovernanceBenchmarkResultV1) -> str:
    return result.model_dump_json(indent=2, exclude_none=False) + "\n"


def run_governance_benchmark(
    catalog: GovernanceBenchmarkCatalogV1,
    *,
    catalog_path: Path | None = None,
    options: GovernanceBenchmarkOptions | None = None,
    work_root: Path | None = None,
) -> GovernanceBenchmarkResultV1:
    selected_options = options or GovernanceBenchmarkOptions()
    selected_cases = _select_cases(catalog, selected_options)
    results: list[GovernanceCaseResultV1] = []
    if work_root is not None:
        work_root.mkdir(parents=True, exist_ok=True)
        for case in selected_cases:
            results.append(_run_case(case, work_root, strict=selected_options.strict))
    else:
        with tempfile.TemporaryDirectory(prefix="agents-shipgate-governance-") as tmp:
            root = Path(tmp)
            for case in selected_cases:
                results.append(_run_case(case, root, strict=selected_options.strict))

    summary = _summary(catalog, results, selected_count=len(selected_cases))
    return GovernanceBenchmarkResultV1(
        catalog_schema_version=catalog.schema_version,
        catalog_path=_display_path(catalog_path) if catalog_path else None,
        summary=summary,
        metrics=_metric_summaries(results),
        cases=results,
    )


def benchmark_exit_code(result: GovernanceBenchmarkResultV1) -> int:
    return 1 if result.summary.failed_cases else 0


def _select_cases(
    catalog: GovernanceBenchmarkCatalogV1,
    options: GovernanceBenchmarkOptions,
):
    cases = []
    for case in catalog.cases:
        if options.case_ids and case.id not in options.case_ids:
            continue
        if options.categories and case.category not in options.categories:
            continue
        if not options.include_catalog_only and case.status != "executable":
            continue
        cases.append(case)
    return cases


def _run_case(case, work_root: Path, *, strict: bool) -> GovernanceCaseResultV1:
    expected = {
        "decision": case.decision,
        "merge_verdict": case.merge_verdict,
        "next_actor": case.next_actor,
    }
    if case.status != "executable":
        failures = [f"case status is {case.status}; no executable fixture is available"] if strict else []
        return GovernanceCaseResultV1(
            id=case.id,
            category=case.category,
            title=case.title,
            status=case.status,
            fixture=case.fixture,
            skipped=True,
            passed=not failures,
            failures=failures,
            expected=expected,
        )

    repo = work_root / case.id
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    failures: list[str] = []
    try:
        _materialize_case(case.fixture or "", repo)
        verifier = _run_verifier(repo)
        lock_diff = _build_lock_diff(repo)
    except (ConfigError, InputParseError) as exc:
        failures.append(str(exc))
        return GovernanceCaseResultV1(
            id=case.id,
            category=case.category,
            title=case.title,
            status=case.status,
            fixture=case.fixture,
            passed=False,
            failures=failures,
            expected=expected,
        )

    actual = _actual_gate(verifier)
    lock_summary = _actual_lock_diff(lock_diff)
    failures.extend(_gate_failures(case, actual))
    capability_failures = _capability_failures(case.capability_expectations, lock_diff)
    failures.extend(capability_failures)
    metric_results = _case_metric_results(
        case.metrics,
        verifier=verifier,
        actual=actual,
        capability_failures=capability_failures,
        expected_tool_names=tuple(
            expectation.tool_name
            for expectation in case.capability_expectations
            if expectation.tool_name
        ),
    )
    failures.extend(
        f"metric {metric.metric} failed: {metric.rationale}"
        for metric in metric_results
        if not metric.passed
    )
    return GovernanceCaseResultV1(
        id=case.id,
        category=case.category,
        title=case.title,
        status=case.status,
        fixture=case.fixture,
        passed=not failures,
        failures=sorted(set(failures)),
        expected=expected,
        actual=actual,
        capability_diff=lock_summary,
        metric_results=metric_results,
    )


def _run_verifier(repo: Path) -> VerifierArtifact:
    verifier, _report, _exit_code = run_verify(
        workspace=repo,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="HEAD",
        archive_head=True,
        out=Path("agents-shipgate-reports"),
        ci_mode=None,
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=False,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        verbose=False,
        pr_comment_style="capability-review",
    )
    return verifier


def _build_lock_diff(repo: Path) -> CapabilityLockDiffV1:
    head = _git(repo, "rev-parse", "HEAD")
    base = _git(repo, "rev-parse", "origin/main")
    try:
        _git(repo, "checkout", "-q", base)
        base_lock = _build_lock_from_config(
            config=repo / "shipgate.yaml",
            no_plugins=True,
            verbose=False,
        )
        _git(repo, "checkout", "-q", head)
        head_lock = _build_lock_from_config(
            config=repo / "shipgate.yaml",
            no_plugins=True,
            verbose=False,
        )
    finally:
        _git(repo, "checkout", "-q", head)
    return diff_capability_locks(
        base_lock,
        head_lock,
        base_path=Path("origin/main"),
        head_path=Path("HEAD"),
    )


def _actual_gate(verifier: VerifierArtifact) -> GovernanceGateActualV1:
    next_actor = verifier.first_next_action.actor if verifier.first_next_action else None
    fix_actor = verifier.fix_task.actor if verifier.fix_task else None
    fix_safe = verifier.fix_task.safe_to_attempt if verifier.fix_task else None
    return GovernanceGateActualV1(
        decision=verifier.decision,
        merge_verdict=verifier.merge_verdict,
        next_actor=next_actor,
        can_merge_without_human=verifier.can_merge_without_human,
        fix_task_actor=fix_actor,
        fix_task_safe_to_attempt=fix_safe,
    )


def _actual_lock_diff(diff: CapabilityLockDiffV1) -> GovernanceCapabilityDiffActualV1:
    return GovernanceCapabilityDiffActualV1(
        added=diff.summary.added,
        removed=diff.summary.removed,
        reidentified=diff.summary.reidentified,
        changed=diff.summary.changed,
        evidence_changed=diff.summary.evidence_changed,
        unchanged=diff.summary.unchanged,
    )


def _gate_failures(case, actual: GovernanceGateActualV1) -> list[str]:
    failures: list[str] = []
    if actual.decision != case.decision:
        failures.append(f"expected decision {case.decision}, got {actual.decision}")
    if actual.merge_verdict != case.merge_verdict:
        failures.append(
            f"expected merge_verdict {case.merge_verdict}, got {actual.merge_verdict}"
        )
    if actual.next_actor != case.next_actor:
        failures.append(f"expected next_actor {case.next_actor}, got {actual.next_actor}")
    return failures


def _capability_failures(
    expectations: Iterable[CapabilityExpectationV1],
    diff: CapabilityLockDiffV1,
) -> list[str]:
    failures: list[str] = []
    for expectation in expectations:
        if not _matching_capability_entries(expectation, diff):
            failures.append(
                "capability expectation did not match: "
                f"{expectation.description or expectation.model_dump(mode='json')}"
            )
    return failures


def _matching_capability_entries(
    expectation: CapabilityExpectationV1,
    diff: CapabilityLockDiffV1,
) -> list[CapabilityFactV1 | CapabilityLockChangedFact]:
    entries = _entries_for_bucket(expectation.bucket, diff)
    return [entry for entry in entries if _matches_capability_expectation(expectation, entry)]


def _entries_for_bucket(
    bucket: str,
    diff: CapabilityLockDiffV1,
) -> list[CapabilityFactV1 | CapabilityLockChangedFact]:
    if bucket == "added":
        return list(diff.added)
    if bucket == "removed":
        return list(diff.removed)
    if bucket == "changed":
        return list(diff.changed)
    if bucket == "reidentified":
        return list(diff.reidentified)
    if bucket == "evidence_changed":
        return list(diff.evidence_changed)
    raise AssertionError(f"unknown capability expectation bucket: {bucket}")


def _matches_capability_expectation(
    expectation: CapabilityExpectationV1,
    entry: CapabilityFactV1 | CapabilityLockChangedFact,
) -> bool:
    facts = _candidate_facts(expectation, entry)
    if not any(_matches_fact(expectation, fact) for fact in facts):
        return False
    if isinstance(entry, CapabilityLockChangedFact):
        if expectation.changed_hashes_contains and not set(
            expectation.changed_hashes_contains
        ).issubset(set(entry.changed_hashes)):
            return False
        if (
            expectation.semantic_direction is not None
            and entry.semantic_direction != expectation.semantic_direction
        ):
            return False
        fields = {change.field for change in entry.semantic_changes}
        if not set(expectation.semantic_change_fields_contains).issubset(fields):
            return False
    elif (
        expectation.changed_hashes_contains
        or expectation.semantic_direction is not None
        or expectation.semantic_change_fields_contains
    ):
        return False
    return True


def _candidate_facts(
    expectation: CapabilityExpectationV1,
    entry: CapabilityFactV1 | CapabilityLockChangedFact,
) -> tuple[CapabilityFactV1, ...]:
    if isinstance(entry, CapabilityFactV1):
        return (entry,)
    if expectation.side == "before":
        return (entry.before,)
    if expectation.side == "either":
        return (entry.before, entry.after)
    return (entry.after,)


def _matches_fact(expectation: CapabilityExpectationV1, fact: CapabilityFactV1) -> bool:
    if expectation.tool_name is not None and fact.identity.tool_name != expectation.tool_name:
        return False
    if expectation.operation is not None and fact.identity.operation != expectation.operation:
        return False
    if expectation.provider is not None and fact.identity.provider != expectation.provider:
        return False
    if expectation.effect is not None and fact.effect.effect != expectation.effect:
        return False
    if expectation.high_risk is not None and fact.effect.high_risk != expectation.high_risk:
        return False
    if expectation.financial is not None and fact.effect.financial != expectation.financial:
        return False
    if (
        expectation.code_execution is not None
        and fact.effect.code_execution != expectation.code_execution
    ):
        return False
    if expectation.broad_scope is not None:
        has_broad_scope = bool(fact.authority.broad_scopes)
        if has_broad_scope != expectation.broad_scope:
            return False
    scopes = set(fact.identity.scope) | set(fact.authority.scopes)
    if not set(expectation.scope_contains).issubset(scopes):
        return False
    if set(expectation.scope_absent).intersection(scopes):
        return False
    if not set(expectation.risk_tags_contains).issubset(set(fact.risk_tags)):
        return False
    return True


def _case_metric_results(
    metrics: Iterable[GovernanceMetricName],
    *,
    verifier: VerifierArtifact,
    actual: GovernanceGateActualV1,
    capability_failures: list[str],
    expected_tool_names: tuple[str, ...],
) -> list[GovernanceMetricResultV1]:
    return [
        _evaluate_metric(
            metric,
            verifier=verifier,
            actual=actual,
            capability_failures=capability_failures,
            expected_tool_names=expected_tool_names,
        )
        for metric in sorted(metrics)
    ]


def _evaluate_metric(
    metric: GovernanceMetricName,
    *,
    verifier: VerifierArtifact,
    actual: GovernanceGateActualV1,
    capability_failures: list[str],
    expected_tool_names: tuple[str, ...],
) -> GovernanceMetricResultV1:
    if metric == "unsafe_merge_prevention":
        passed = actual.merge_verdict != "mergeable" and not bool(
            actual.can_merge_without_human
        )
        return GovernanceMetricResultV1(
            metric=metric,
            passed=passed,
            rationale="unsafe case did not auto-merge" if passed else "unsafe case auto-merged",
        )
    if metric == "safe_pass_rate":
        passed = actual.merge_verdict == "mergeable" and bool(actual.can_merge_without_human)
        return GovernanceMetricResultV1(
            metric=metric,
            passed=passed,
            rationale="safe case was mergeable" if passed else "safe case required review",
        )
    if metric == "authority_routing":
        passed = actual.next_actor == "human"
        return GovernanceMetricResultV1(
            metric=metric,
            passed=passed,
            rationale=(
                "authority gap routed to human"
                if passed
                else f"authority gap routed to {actual.next_actor}"
            ),
        )
    if metric == "explanation_usefulness":
        passed = _explains_expected_subject(verifier, expected_tool_names)
        return GovernanceMetricResultV1(
            metric=metric,
            passed=passed,
            rationale=(
                "verifier evidence references expected subject"
                if passed
                else "verifier evidence did not reference expected subject"
            ),
        )
    if metric == "remediation_boundary":
        passed = actual.fix_task_actor == "human" and actual.fix_task_safe_to_attempt is False
        return GovernanceMetricResultV1(
            metric=metric,
            passed=passed,
            rationale=(
                "human-routed gap is not agent-safe"
                if passed
                else "human-routed gap was marked agent-safe"
            ),
        )
    if metric == "capability_semantic_fidelity":
        passed = not capability_failures
        return GovernanceMetricResultV1(
            metric=metric,
            passed=passed,
            rationale="capability expectations matched" if passed else "; ".join(capability_failures),
        )
    raise AssertionError(f"unknown governance metric: {metric}")


def _explains_expected_subject(
    verifier: VerifierArtifact,
    expected_tool_names: tuple[str, ...],
) -> bool:
    release_items = []
    if verifier.release_decision:
        release_items.extend(verifier.release_decision.get("blockers", []))
        release_items.extend(verifier.release_decision.get("review_items", []))
    haystack = json.dumps(
        {
            "top_changes": [
                change.model_dump(mode="json")
                for change in verifier.capability_review.top_changes
            ],
            "release_items": release_items,
        },
        sort_keys=True,
    )
    if expected_tool_names:
        return any(name in haystack for name in expected_tool_names)
    return bool(release_items) or verifier.merge_verdict == "mergeable"


def _summary(
    catalog: GovernanceBenchmarkCatalogV1,
    results: list[GovernanceCaseResultV1],
    *,
    selected_count: int,
) -> GovernanceBenchmarkSummaryV1:
    status_counts: dict[GovernanceCaseStatus, int] = defaultdict(int)
    for case in catalog.cases:
        status_counts[case.status] += 1
    return GovernanceBenchmarkSummaryV1(
        total_cases=len(catalog.cases),
        executable_cases=status_counts["executable"],
        catalog_only_cases=status_counts["catalog_only"],
        external_evidence_cases=status_counts["external_evidence"],
        selected_cases=selected_count,
        passed_cases=sum(1 for result in results if result.passed and not result.skipped),
        failed_cases=sum(1 for result in results if not result.passed),
        skipped_cases=sum(1 for result in results if result.skipped),
    )


def _metric_summaries(
    results: list[GovernanceCaseResultV1],
) -> list[GovernanceBenchmarkMetricSummaryV1]:
    grouped: dict[GovernanceMetricName, list[GovernanceMetricResultV1]] = defaultdict(list)
    for result in results:
        for metric in result.metric_results:
            grouped[metric.metric].append(metric)
    return [
        GovernanceBenchmarkMetricSummaryV1(
            metric=metric,
            total=len(rows),
            passed=sum(1 for row in rows if row.passed),
            failed=sum(1 for row in rows if not row.passed),
        )
        for metric, rows in sorted(grouped.items())
    ]


def _materialize_case(fixture: str, repo: Path) -> None:
    if fixture not in _FIXTURES:
        raise ConfigError(f"Unknown governance benchmark fixture: {fixture}")
    trees = _FIXTURES[fixture]()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "benchmark@example.com")
    _git(repo, "config", "user.name", "Governance Benchmark")
    _apply_files(repo, trees.base_files)
    _commit(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _apply_files(repo, trees.head_files)
    _commit(repo, "head")


def _apply_files(repo: Path, files: dict[str, str | None]) -> None:
    for relative, content in sorted(files.items()):
        path = repo / relative
        if content is None:
            if path.exists():
                path.unlink()
            _prune_empty_parents(path.parent, repo)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def _prune_empty_parents(path: Path, root: Path) -> None:
    while path != root and path.exists():
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path) if not path.is_absolute() else path.name


def _manifest(*, environment: str = "production_like", ci: str = "advisory") -> str:
    return f"""version: "0.1"
project:
  name: governance-benchmark
agent:
  name: governance-agent
  declared_purpose:
    - benchmark deterministic governance behavior
environment:
  target: {environment}
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
    trust: internal
ci:
  mode: {ci}
"""


def _manifest_with_scopes(scopes: list[str]) -> str:
    rendered_scopes = "\n".join(f"    - {scope}" for scope in scopes)
    return f"""version: "0.1"
project:
  name: governance-benchmark
agent:
  name: governance-agent
  declared_purpose:
    - benchmark deterministic governance behavior
environment:
  target: production_like
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
    trust: internal
permissions:
  scopes:
{rendered_scopes}
ci:
  mode: advisory
"""


def _strict_manifest() -> str:
    return """version: "0.1"
project:
  name: governance-benchmark
agent:
  name: governance-agent
  declared_purpose:
    - benchmark deterministic governance behavior
environment:
  target: production_like
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
    trust: internal
ci:
  mode: strict
  fail_on:
    - critical
    - high
"""


def _tools(tools: list[dict[str, Any]]) -> str:
    return json.dumps({"tools": tools}, indent=2, sort_keys=True) + "\n"


def _tool(template: dict[str, Any], **updates: Any) -> dict[str, Any]:
    tool = deepcopy(template)
    tool.update(updates)
    return tool


_READ_TOOL = {
    "name": "support.search_kb",
    "description": "Search support knowledge base articles without changing records.",
    "annotations": {"readOnlyHint": True, "idempotentHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {"q": {"type": "string"}},
    },
}

_SAFE_LOOKUP_TOOL = {
    "name": "support.lookup_case",
    "description": "Look up one support case by id without modifying any records.",
    "annotations": {"readOnlyHint": True, "idempotentHint": True},
    "inputSchema": {
        "type": "object",
        "required": ["case_id"],
        "properties": {"case_id": {"type": "string"}},
    },
}

_REFUND_TOOL = {
    "name": "stripe.create_refund",
    "description": "Create a real money refund to the customer payment method.",
    "annotations": {"readOnlyHint": False, "destructiveHint": True},
    "inputSchema": {
        "type": "object",
        "required": ["charge_id", "amount"],
        "properties": {
            "charge_id": {"type": "string"},
            "amount": {"type": "number"},
        },
    },
    "auth": {"type": "oauth2", "scopes": ["stripe:*"]},
}

_DELETE_TOOL = {
    "name": "support.delete_case",
    "description": "Permanently delete a customer support case.",
    "annotations": {"readOnlyHint": False, "destructiveHint": True},
    "inputSchema": {
        "type": "object",
        "required": ["case_id"],
        "properties": {"case_id": {"type": "string"}},
    },
}

_DEPLOY_TOOL = {
    "name": "infra.deploy_production",
    "description": "Deploy application code to the production environment.",
    "annotations": {"readOnlyHint": False, "destructiveHint": True},
    "inputSchema": {
        "type": "object",
        "required": ["version"],
        "properties": {"version": {"type": "string"}},
    },
}

_BROAD_LIST_CASES_TOOL = {
    "name": "support.list_cases",
    "description": "List support case metadata by status for support review without modifying records.",
    "annotations": {"readOnlyHint": True, "idempotentHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["open", "closed"]}},
    },
    "auth": {"type": "oauth2", "scopes": ["cases:read", "cases:write"]},
}

_NARROW_LIST_CASES_TOOL = _tool(
    _BROAD_LIST_CASES_TOOL,
    auth={"type": "oauth2", "scopes": ["cases:read"]},
)

_WORKFLOW = """name: agents-shipgate
on: [pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ./
        with:
          config: shipgate.yaml
          ci_mode: advisory
"""


def _base_read_files() -> dict[str, str | None]:
    return {
        "shipgate.yaml": _manifest(environment="local"),
        "tools.json": _tools([_READ_TOOL]),
    }


def _added_tool_fixture(tool: dict[str, Any]) -> _FixtureTrees:
    base = _base_read_files()
    return _FixtureTrees(
        base_files=base,
        head_files={"tools.json": _tools([_READ_TOOL, tool])},
    )


def _mcp_refund_tool_added() -> _FixtureTrees:
    return _added_tool_fixture(_REFUND_TOOL)


def _mcp_destructive_tool_added() -> _FixtureTrees:
    return _added_tool_fixture(_DELETE_TOOL)


def _infra_prod_deploy_tool_added() -> _FixtureTrees:
    return _added_tool_fixture(_DEPLOY_TOOL)


def _mcp_safe_read_tool_added() -> _FixtureTrees:
    return _added_tool_fixture(_SAFE_LOOKUP_TOOL)


def _benign_remove_dangerous_tool() -> _FixtureTrees:
    return _FixtureTrees(
        base_files={
            "shipgate.yaml": _manifest(environment="local"),
            "tools.json": _tools([_READ_TOOL, _DELETE_TOOL]),
        },
        head_files={"tools.json": _tools([_READ_TOOL])},
    )


def _benign_narrow_scope() -> _FixtureTrees:
    return _FixtureTrees(
        base_files={
            "shipgate.yaml": _manifest_with_scopes(["cases:read"]),
            "tools.json": _tools([_BROAD_LIST_CASES_TOOL]),
        },
        head_files={"tools.json": _tools([_NARROW_LIST_CASES_TOOL])},
    )


def _ci_shipgate_workflow_removed() -> _FixtureTrees:
    base = _base_read_files()
    base[".github/workflows/agents-shipgate.yml"] = _WORKFLOW
    return _FixtureTrees(
        base_files=base,
        head_files={".github/workflows/agents-shipgate.yml": None},
    )


def _ci_advisory_from_strict() -> _FixtureTrees:
    return _FixtureTrees(
        base_files={
            "shipgate.yaml": _strict_manifest(),
            "tools.json": _tools([_READ_TOOL]),
        },
        head_files={"shipgate.yaml": _manifest(environment="local")},
    )


def _instr_policy_suppression_added() -> _FixtureTrees:
    return _FixtureTrees(
        base_files=_base_read_files(),
        head_files={
            "shipgate.yaml": _manifest(environment="local")
            + "checks:\n"
            + "  ignore:\n"
            + "    - check_id: SHIP-POLICY-APPROVAL-MISSING\n"
            + "      reason: accepted for governance benchmark\n"
        },
    )


def _benign_docs_opted_in() -> _FixtureTrees:
    base = _base_read_files()
    base["README.md"] = "base documentation\n"
    return _FixtureTrees(
        base_files=base,
        head_files={"README.md": "head documentation\n"},
    )


_FIXTURES = {
    "mcp_refund_tool_added": _mcp_refund_tool_added,
    "mcp_destructive_tool_added": _mcp_destructive_tool_added,
    "infra_prod_deploy_tool_added": _infra_prod_deploy_tool_added,
    "mcp_safe_read_tool_added": _mcp_safe_read_tool_added,
    "benign_remove_dangerous_tool": _benign_remove_dangerous_tool,
    "benign_narrow_scope": _benign_narrow_scope,
    "ci_shipgate_workflow_removed": _ci_shipgate_workflow_removed,
    "ci_advisory_from_strict": _ci_advisory_from_strict,
    "instr_policy_suppression_added": _instr_policy_suppression_added,
    "benign_docs_opted_in": _benign_docs_opted_in,
}


__all__ = [
    "GovernanceBenchmarkOptions",
    "benchmark_exit_code",
    "load_governance_benchmark_catalog",
    "render_governance_benchmark_result_json",
    "run_governance_benchmark",
]
