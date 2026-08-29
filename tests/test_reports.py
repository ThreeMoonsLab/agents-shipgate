import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import validate

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.current_control import (
    CurrentControlUnavailable,
    read_current_control,
)
from agents_shipgate.report.markdown import _safe_markdown_text, render_markdown_report
from agents_shipgate.schemas.current_control import CurrentControlPointer
from agents_shipgate.schemas.report import ReadinessReport

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")
EXPECTED_MARKDOWN = Path("samples/support_refund_agent/expected/report.md")
OPENAI_API_SAMPLE = Path("samples/simple_openai_api_agent/shipgate.yaml")
OPENAI_API_EXPECTED_MARKDOWN = Path("samples/simple_openai_api_agent/expected/report.md")
LANGCHAIN_SAMPLE = Path("samples/simple_langchain_agent/shipgate.yaml")
LANGCHAIN_EXPECTED_MARKDOWN = Path("samples/simple_langchain_agent/expected/report.md")
CREWAI_SAMPLE = Path("samples/simple_crewai_agent/shipgate.yaml")
CREWAI_EXPECTED_MARKDOWN = Path("samples/simple_crewai_agent/expected/report.md")
REPORT_SCHEMA = Path("docs/report-schema.v0.1.json")
REPORT_SCHEMA_V02 = Path("docs/report-schema.v0.2.json")
REPORT_SCHEMA_V04 = Path("docs/report-schema.v0.4.json")
REPORT_SCHEMA_V06 = Path("docs/report-schema.v0.6.json")
REPORT_SCHEMA_V07 = Path("docs/report-schema.v0.7.json")
REPORT_SCHEMA_V08 = Path("docs/report-schema.v0.8.json")
REPORT_SCHEMA_V09 = Path("docs/report-schema.v0.9.json")
REPORT_SCHEMA_V10 = Path("docs/report-schema.v0.10.json")
REPORT_SCHEMA_V11 = Path("docs/report-schema.v0.11.json")
REPORT_SCHEMA_V12 = Path("docs/report-schema.v0.12.json")
REPORT_SCHEMA_V13 = Path("docs/report-schema.v0.13.json")
REPORT_SCHEMA_V14 = Path("docs/report-schema.v0.14.json")
REPORT_SCHEMA_V15 = Path("docs/report-schema.v0.15.json")
REPORT_SCHEMA_V16 = Path("docs/report-schema.v0.16.json")
REPORT_SCHEMA_V17 = Path("docs/report-schema.v0.17.json")
REPORT_SCHEMA_V18 = Path("docs/report-schema.v0.18.json")
REPORT_SCHEMA_V19 = Path("docs/report-schema.v0.19.json")
REPORT_SCHEMA_V20 = Path("docs/report-schema.v0.20.json")
REPORT_SCHEMA_V21 = Path("docs/report-schema.v0.21.json")
CURRENT_REPORT_SCHEMA_VERSION = str(
    ReadinessReport.model_fields["report_schema_version"].default
)
# Tracks the live model default so version bumps don't strand these tests
# on a stale schema file (the failure mode being fixed in v0.22).
REPORT_SCHEMA_CURRENT = Path(
    f"docs/report-schema.v{CURRENT_REPORT_SCHEMA_VERSION}.json"
)


def test_sample_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    actual = actual.replace(str(Path.cwd()), "<REPO>")
    expected = EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


def test_markdown_keeps_intent_diff_sections_when_runtime_traces_absent(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.misalignments
    assert not report.capability_runtime_evidence.enabled

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    markers = [
        "## Capability <-> Intent Diff",
        "\nActual capabilities:\n\n",
        "\nPolicy/control gaps:\n\n",
        "\nRelease implication:\n\n- Decision: blocked",
        "\nNext validation:\n\n",
        "## Recommended Next Actions",
        "## Capability Runtime Evidence",
    ]
    positions = [markdown.index(marker) for marker in markers]

    assert positions == sorted(positions)
    assert "stripe.create\\_refund lacks a declared approval policy." in markdown
    assert "stripe.create\\_refund lacks idempotency evidence." in markdown
    assert (
        "No local runtime trace artifacts were declared for capability evidence."
        in markdown
    )


def test_openai_api_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=OPENAI_API_SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    expected = OPENAI_API_EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


def test_langchain_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=LANGCHAIN_SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    expected = LANGCHAIN_EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


def test_crewai_markdown_report_matches_golden(tmp_path):
    run_scan(
        config_path=CREWAI_SAMPLE,
        output_dir=tmp_path,
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    actual = (tmp_path / "report.md").read_text(encoding="utf-8")
    expected = CREWAI_EXPECTED_MARKDOWN.read_text(encoding="utf-8")

    assert actual == expected


@pytest.mark.parametrize(
    "sample_dir, expected_decision",
    [
        ("samples/simple_openai_api_agent", "blocked"),
        ("samples/simple_langchain_agent", "passed"),
        ("samples/simple_crewai_agent", "review_required"),
        ("samples/support_refund_agent", "blocked"),
    ],
)
def test_sample_expected_report_json_is_current(sample_dir, expected_decision):
    """Pin the expected JSON goldens to the current report schema version
    and to the documented decision. The markdown golden tests catch
    rendering drift; this catches the JSON drift the reviewer flagged on
    PR #70 (langchain/crewai/openai expected JSON had been left at
    schema 0.13 with the pre-v0.14 decision after the markdown was
    regenerated)."""
    golden = json.loads(
        (Path(sample_dir) / "expected" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    assert golden["report_schema_version"] == CURRENT_REPORT_SCHEMA_VERSION, (
        f"{sample_dir}/expected/report.json carries "
        f"report_schema_version={golden['report_schema_version']!r}; "
        f"current is {CURRENT_REPORT_SCHEMA_VERSION!r}. Regenerate the "
        "golden with `agents-shipgate scan` and commit."
    )
    assert golden["release_decision"]["decision"] == expected_decision, (
        f"{sample_dir}/expected/report.json carries decision "
        f"{golden['release_decision']['decision']!r}; expected "
        f"{expected_decision!r}. If the threshold tuning changed or the "
        "sample evolved, update both the golden and the expected value."
    )


@pytest.mark.parametrize(
    "sample_dir",
    [
        "samples/simple_openai_api_agent",
        "samples/simple_langchain_agent",
        "samples/simple_crewai_agent",
        "samples/support_refund_agent",
    ],
)
def test_sample_expected_report_json_has_no_structural_drift(sample_dir, tmp_path):
    """A version stamp is not proof the body was regenerated.

    ``test_sample_expected_report_json_is_current`` checks the version string
    and the decision, and its own message says to regenerate the golden — but
    both assertions pass after a one-line relabel. A report-schema bump that
    only edited ``report_schema_version`` therefore shipped four goldens
    claiming v0.37 while carrying no ``declaration_questions`` and no
    ``observed_readings`` member anywhere.

    Compares the *shape* rather than the values: a fresh scan's set of field
    paths must equal the golden's. Values legitimately differ between runs
    (paths, hashes, timestamps) and are covered by the markdown and packet
    byte-comparisons and by the Conductor field-for-field check; a missing or
    extra field is drift no value comparison would report.
    """
    from agents_shipgate.cli.scan import run_scan

    run_scan(
        config_path=Path(sample_dir) / "shipgate.yaml",
        output_dir=tmp_path,
        # The formats the committed goldens are generated with:
        # ``generated_reports`` records them, so scanning with a narrower set
        # would report a run-configuration difference as drift.
        formats=["json", "markdown"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    fresh = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    golden = json.loads(
        (Path(sample_dir) / "expected" / "report.json").read_text(encoding="utf-8")
    )

    def paths(node, prefix=""):
        # List indices are collapsed: a golden with one finding and a fresh
        # scan with two are not structurally different, but a *member* the
        # golden has never seen is.
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{prefix}.{key}" if prefix else str(key)
                yield child
                yield from paths(value, child)
        elif isinstance(node, list):
            for value in node:
                yield from paths(value, f"{prefix}[]")

    missing = sorted(set(paths(fresh)) - set(paths(golden)))
    extra = sorted(set(paths(golden)) - set(paths(fresh)))

    assert not missing and not extra, (
        f"{sample_dir}/expected/report.json is stale. Fields a fresh scan "
        f"emits that the golden lacks: {missing}. Fields the golden carries "
        f"that a fresh scan does not: {extra}. Regenerate it with a real scan "
        "rather than editing the version stamp."
    )


def test_sample_expected_report_json_uses_repo_placeholder_for_manifest_dir():
    """Sample goldens should not expose or churn on contributor home paths."""
    for path in sorted(Path("samples").glob("*/expected/report.json")):
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        assert str(Path.cwd()) not in text
        assert payload["manifest_dir"].startswith("<REPO>/samples/")


# What each hash-bound fixture's pointer must bind, declared per fixture rather
# than read off the pointer itself.  An empty `artifacts` map is schema-valid
# for a `scan` pointer -- `CurrentControlPointer` only requires a binding when
# the operation is `verify` or `preview` -- so a pointer that dropped its refs
# would sail through the loop below without checking anything.  Naming the keys
# here is what makes removing one a test failure instead of a silent pass.
EXPECTED_CURRENT_CONTROL_ARTIFACTS = {
    "conductor_agent": {"report", "report_markdown"},
}


def test_sample_current_control_pointers_bind_the_committed_artifacts():
    """A shipped pointer must hash the bytes committed beside it.

    The goldens are written by a real run and *then* normalized -- the
    contributor path under `manifest_dir` is replaced with `<REPO>` so the
    fixtures do not churn on whose checkout produced them.  That rewrite
    changes both the length and the digest of `report.json`, so a pointer
    hashed before the substitution describes a file that is never committed
    and drifts with the generating checkout's path length.

    Two oracles, because neither one alone is enough.  The digests are recomputed
    with plain `hashlib`, deliberately not with `bind_current_control_artifacts`:
    an oracle that reuses the producer agrees with a broken producer.  Then the
    real reader runs, because hashing here follows symlinks and `read_current_
    control()` refuses them -- a fixture can satisfy every digest above while the
    product still rejects it as unsafe to read.
    """
    pointers = sorted(Path("samples").glob("*/expected/current-control.json"))
    assert pointers, (
        "No samples/*/expected/current-control.json was found, so this "
        "invariant is vacuous. If the sample moved, repoint the glob rather "
        "than leaving the check to pass on an empty set."
    )

    for path in pointers:
        sample = path.parent.parent.name
        assert sample in EXPECTED_CURRENT_CONTROL_ARTIFACTS, (
            f"{path} is a hash-bound fixture this guard does not know about. "
            f"Add {sample!r} to EXPECTED_CURRENT_CONTROL_ARTIFACTS naming the "
            "artifacts it binds, so the checks below have something to assert."
        )
        # Parsing through the model is part of the guard: `current_control_id`
        # hashes the artifact refs, so a digest corrected by hand without a
        # recomputed identity fails right here.
        pointer = CurrentControlPointer.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        assert set(pointer.artifacts) == EXPECTED_CURRENT_CONTROL_ARTIFACTS[sample], (
            f"{path} binds {sorted(pointer.artifacts)}, but this fixture is "
            f"declared to bind {sorted(EXPECTED_CURRENT_CONTROL_ARTIFACTS[sample])}. "
            "A scan pointer may legally bind nothing at all, so dropping a "
            "binding is a deliberate change to make here, not a silent one."
        )

        for key, ref in sorted(pointer.artifacts.items()):
            bound = path.parent / ref.path
            # `is_file()` and `read_bytes()` below both follow symlinks; the
            # reader does not. Rejecting the link here says which file is wrong.
            assert not bound.is_symlink(), (
                f"{path} binds {key!r} to {ref.path}, which is a symlink. The "
                "reader refuses symlinked artifacts, so the bytes hashed here "
                "would not be the bytes it agrees to read."
            )
            assert bound.is_file(), (
                f"{path} binds {key!r} to {ref.path}, which is not committed "
                f"beside it."
            )
            data = bound.read_bytes()
            actual = "sha256:" + hashlib.sha256(data).hexdigest()
            assert (actual, len(data)) == (ref.sha256, ref.size_bytes), (
                f"{path} binds {key!r} to {ref.path} as {ref.sha256} "
                f"({ref.size_bytes} bytes), but the committed file is "
                f"{actual} ({len(data)} bytes). Regenerate the pointer after "
                "the `<REPO>` substitution, not before it, and recompute "
                "`current_control_id` with `current_control_identity_payload` "
                "rather than editing the digest in place. If the size differs "
                "by about one byte per line, the checkout translated the line "
                "endings -- see the `-text` pin in `.gitattributes`."
            )

        # The end-to-end property the digests are only a proxy for: Shipgate
        # accepts its own shipped sample. This is what actually broke, and it
        # fails on safe-read rules the byte checks above cannot model.
        try:
            read_current_control(path.parent)
        except CurrentControlUnavailable as exc:
            raise AssertionError(
                f"{path} binds digests that match the committed files, but "
                f"read_current_control() still refuses the set: {exc}"
            ) from exc


def test_json_report_contains_integration_contract_keys(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report.model_dump(mode="json")

    assert payload["summary"]["status"] == "release_blockers_detected"
    assert "critical_count" in payload["summary"]
    assert "title" in payload["findings"][0]
    assert "severity" in payload["findings"][0]
    assert "fingerprint" in payload["findings"][0]
    assert "tool_inventory" in payload
    assert "loaded_plugins" in payload
    assert payload["loaded_plugins"] == []
    assert payload["schema_version"] == "0.1"
    assert payload["report_schema_version"] == CURRENT_REPORT_SCHEMA_VERSION
    assert "release_decision" in payload
    assert payload["release_decision"]["decision"] in {
        "blocked",
        "review_required",
        "passed",
    }
    assert "frameworks" in payload
    assert "loaded_policy_packs" in payload
    for key in (
        "capability_facts",
        "declared_intentions",
        "misalignments",
        "release_consequence",
        "suggested_scenarios",
        "tool_surface_facts",
        "tool_surface_diff",
        "action_surface_facts",
        "action_surface_diff",
    ):
        assert key in payload


def test_markdown_release_summary_is_derived_from_json_contract(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)
    decision = payload["release_decision"]
    fail_policy = decision["fail_policy"]
    markdown = render_markdown_report(report)

    assert f"Decision: {decision['decision']}" in markdown
    assert f"Blockers ({len(decision['blockers'])}):" in markdown
    assert f"Review items ({len(decision['review_items'])}):" in markdown
    fail_on_text = (
        ", ".join(fail_policy["fail_on"]) if fail_policy["fail_on"] else "none"
    )
    assert (
        f"Fail policy: ci_mode={fail_policy['ci_mode']}, fail_on=[{fail_on_text}], "
        f"new_findings_only={str(fail_policy['new_findings_only']).lower()}, "
        f"would_fail_ci={str(fail_policy['would_fail_ci']).lower()} "
        f"(exit {fail_policy['exit_code']})"
    ) in markdown


def test_capability_intent_diff_support_refund_fixture(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)

    stripe = next(
        fact
        for fact in payload["capability_facts"]
        if fact["tool_name"] == "stripe.create_refund"
    )
    assert {"financial_action", "external_write"} <= set(stripe["risk_tags"])
    assert stripe["included_reason"] == "referenced_by_critical_finding"
    assert stripe["control_status"] == "missing"
    refund_lookup = next(
        fact
        for fact in payload["capability_facts"]
        if fact["tool_name"] == "refund_status_lookup"
    )
    assert refund_lookup["effect"] == "read"
    assert refund_lookup["semantic_assessment"]["authority"]["mode"] == "unscoped"
    assert refund_lookup["semantic_assessment"]["pass_eligible"] is False

    refund_intentions = [
        intention
        for intention in payload["declared_intentions"]
        if "refund" in intention["text"]
    ]
    assert refund_intentions
    assert any("financial_action" in item["intent_tags"] for item in refund_intentions)
    update_ticket_intention = next(
        intention
        for intention in payload["declared_intentions"]
        if intention["text"] == "update support ticket notes"
    )

    approval_finding = next(
        finding
        for finding in payload["findings"]
        if finding["check_id"] == "SHIP-POLICY-APPROVAL-MISSING"
        and finding["tool_name"] == "stripe.create_refund"
    )
    idempotency_finding = next(
        finding
        for finding in payload["findings"]
        if finding["check_id"] == "SHIP-SIDEFX-IDEMPOTENCY-MISSING"
        and finding["tool_name"] == "stripe.create_refund"
    )
    unused_scope_finding = next(
        finding
        for finding in payload["findings"]
        if finding["check_id"] == "SHIP-MANIFEST-UNUSED-SCOPE"
    )
    assert approval_finding["fingerprint"] == "fp_973ea0ef2110ca9a"
    assert idempotency_finding["fingerprint"] == "fp_2cf0d6c77d9c3eee"

    assert any(
        item["kind"] == "policy_gap"
        and approval_finding["id"] in item["finding_refs"]
        for item in payload["misalignments"]
    )
    assert all(
        update_ticket_intention["id"] not in item["intention_refs"]
        for item in payload["misalignments"]
        if item["tool_name"] == "stripe.create_refund"
    )
    assert any(
        item["kind"] == "control_missing"
        and idempotency_finding["id"] in item["finding_refs"]
        for item in payload["misalignments"]
    )
    assert any(
        item["kind"] == "scope_drift"
        and unused_scope_finding["id"] in item["finding_refs"]
        for item in payload["misalignments"]
    )
    scenario_types = {item["scenario_type"] for item in payload["suggested_scenarios"]}
    assert {"approval", "idempotency_retry"} <= scenario_types
    least_privilege = next(
        item
        for item in payload["suggested_scenarios"]
        if item["scenario_type"] == "least_privilege_scope"
    )
    assert unused_scope_finding["id"] in least_privilege["source_findings"]


def test_capability_intent_markdown_pins_prohibited_actions(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    markdown = render_markdown_report(report)

    assert "- prohibited\\_action: issue refund without approval" in markdown
    assert "- prohibited\\_action: send external email without preview" in markdown
    assert (
        "- prohibited\\_action: cancel order without explicit confirmation"
        in markdown
    )


def test_capability_intent_markdown_collapses_multiline_instruction_preview(tmp_path):
    report, _ = run_scan(
        config_path=OPENAI_API_SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    markdown = render_markdown_report(report)

    assert "assistant.\n\nYou should only advise" not in markdown
    assert (
        "- instruction\\_preview: You are a support refund assistant. "
        "You should only advise the support representative"
    ) in markdown


def test_capability_diff_intent_tags_are_alias_based_and_negation_aware():
    from agents_shipgate.core.lenses.capability_intent import _intent_tags

    tags = _intent_tags("send external email without preview")

    assert {"external_write", "customer_communication"} <= set(tags)
    assert "write" not in tags
    assert "read_only" not in tags
    assert "financial_action" not in _intent_tags("do not refund customer")
    assert "destructive" not in _intent_tags("never delete records")
    assert _intent_tags("update support ticket notes") == []
    assert _intent_tags("answer ticket status") == []
    assert "financial_action" in _intent_tags("process reimbursements")


def test_capability_diff_includes_framework_release_blocker_categories():
    from agents_shipgate.core.lenses.capability_intent import apply_capability_diff
    from agents_shipgate.schemas.report import (
        Finding,
        ReadinessReport,
        ReportSummary,
        ToolSurfaceSummary,
    )

    report = ReadinessReport(
        run_id="test",
        project={"name": "framework-test"},
        agent={"name": "framework-agent", "declared_purpose": ["use framework tools"]},
        environment={"target": "test"},
        summary=ReportSummary(status="release_review_required", high_count=1),
        tool_surface=ToolSurfaceSummary(total_tools=0, high_risk_tools=0),
        findings=[
            Finding(
                id="finding-langchain-dynamic",
                fingerprint="fp_langchain_dynamic",
                check_id="SHIP-LANGCHAIN-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",
                title="LangChain tool surface cannot be statically enumerated",
                severity="high",
                category="langchain",
                confidence="medium",
                recommendation="Provide explicit inventory metadata.",
            )
        ],
    )

    apply_capability_diff(report, [])

    assert report.misalignments
    assert report.misalignments[0].kind == "control_missing"
    assert report.misalignments[0].finding_refs == ["finding-langchain-dynamic"]
    assert report.suggested_scenarios[0].scenario_type == "wildcard_inventory"


def test_capability_diff_release_consequence_mirrors_release_decision(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)

    assert payload["release_consequence"]["decision"] == payload["release_decision"]["decision"]
    assert (
        payload["release_consequence"]["fail_policy"]
        == payload["release_decision"]["fail_policy"]
    )
    assert payload["release_consequence"]["blocker_misalignment_count"] >= 1
    blockers = payload["release_decision"]["blockers"]
    assert len(blockers) == 5
    # Identity, classification, and release-blocking shape are pinned.
    identity_fields = (
        "id",
        "fingerprint",
        "check_id",
        "severity",
        "title",
        "baseline_status",
        "blocks_release",
    )
    assert tuple(blockers[0][k] for k in identity_fields) == (
        "fp_973ea0ef2110ca9a",
        "fp_973ea0ef2110ca9a",
        "SHIP-POLICY-APPROVAL-MISSING",
        "critical",
        "stripe.create_refund lacks a declared approval policy",
        None,
        False,
    )
    assert tuple(blockers[1][k] for k in identity_fields) == (
            "fp_2cf0d6c77d9c3eee",
            "fp_2cf0d6c77d9c3eee",
        "SHIP-SIDEFX-IDEMPOTENCY-MISSING",
        "critical",
        "stripe.create_refund lacks idempotency evidence",
        None,
        False,
    )
    # v0.19 reviewer-grade provenance: each blocker carries the dual
    # source pointers from the originating Finding (tool source + the
    # missing-mitigation manifest pointer). The exact line numbers
    # depend on the sample manifest layout, so the test pins only
    # presence and pointer identity.
    approval_evidence = blockers[0]["policy_evidence_source"]
    assert approval_evidence is not None
    assert approval_evidence["pointer"] == "/policies/require_approval_for_tools"
    assert approval_evidence["path"] == "shipgate.yaml"
    idempotency_evidence = blockers[1]["policy_evidence_source"]
    assert idempotency_evidence is not None
    assert idempotency_evidence["pointer"] == "/policies/require_idempotency_for_tools"
    assert idempotency_evidence["path"] == "shipgate.yaml"


def test_release_consequence_counts_distinct_release_decision_findings(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=OPENAI_API_SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)

    assert len(payload["misalignments"]) > len(payload["release_decision"]["review_items"])
    assert payload["release_consequence"]["blocker_misalignment_count"] == len(
        payload["release_decision"]["blockers"]
    )
    assert payload["release_consequence"]["review_misalignment_count"] == len(
        payload["release_decision"]["review_items"]
    )


def test_capability_diff_blocks_are_reproducible(tmp_path):
    from agents_shipgate.report.json_report import report_json_payload

    first_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "first",
        formats=["json"],
        ci_mode="advisory",
    )
    second_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "second",
        formats=["json"],
        ci_mode="advisory",
    )
    first = report_json_payload(first_report)
    second = report_json_payload(second_report)
    for key in (
        "capability_facts",
        "declared_intentions",
        "misalignments",
        "release_consequence",
        "suggested_scenarios",
    ):
        assert first[key] == second[key]


def test_report_paths_use_absolute_path_when_output_escapes_manifest_base(tmp_path):
    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )

    assert str(report.generated_reports["json"]).startswith(str(tmp_path))
    assert not str(report.generated_reports["json"]).startswith("..")


def test_json_report_is_reproducible_for_same_inputs(tmp_path):
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    first = (tmp_path / "report.json").read_text(encoding="utf-8")
    run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    second = (tmp_path / "report.json").read_text(encoding="utf-8")

    assert first == second


def test_json_schema_is_published():
    text = REPORT_SCHEMA.read_text(encoding="utf-8")
    schema = json.loads(text)

    assert "Agents Shipgate Readiness Report v0.1" in text
    assert '"schema_version"' in text
    inventory_item = schema["properties"]["tool_inventory"]["items"]
    assert {"name", "source_type", "risk_tags", "confidence"} <= set(
        inventory_item["required"]
    )
    api_surface = schema["properties"]["api_surface"]["anyOf"][0]
    assert {
        "prompt_file_count",
        "tool_file_count",
        "response_format_count",
        "model_config_present",
    } <= set(api_surface["required"])


def test_json_report_validates_against_current_schema(tmp_path):
    """Current schema (v0.19) adds reviewer-grade dual-source provenance
    (``Finding.policy_evidence_source`` and
    ``ReleaseDecisionItem.{source, policy_evidence_source}``) on top of
    v0.18's privacy audit and the v0.17 release decision and policy
    audit fields. Emitted reports must validate against it."""
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    schema = json.loads(REPORT_SCHEMA_CURRENT.read_text(encoding="utf-8"))

    validate(instance=report_json_payload(report), schema=schema)


def test_v14_schema_does_not_include_provenance_kind():
    """v0.14 is a frozen-reference schema; it MUST NOT mention
    provenance_kind. Adding the field to v0.14 retroactively would
    silently break consumers that pin the v0.14 contract."""
    schema = json.loads(REPORT_SCHEMA_V14.read_text(encoding="utf-8"))
    finding_def = schema["$defs"]["Finding"]
    assert "provenance_kind" not in finding_def.get("properties", {})
    assert "provenance_kind" not in finding_def.get("required", [])


def test_v13_schema_does_not_include_provenance_kind():
    """v0.13 is the frozen-reference schema; it MUST NOT mention
    provenance_kind. Adding the field to v0.13 retroactively would
    silently break consumers that pin the v0.13 contract."""
    schema = json.loads(REPORT_SCHEMA_V13.read_text(encoding="utf-8"))
    finding_def = schema["$defs"]["Finding"]
    assert "provenance_kind" not in finding_def.get("properties", {})
    assert "provenance_kind" not in finding_def.get("required", [])


def test_v07_schema_file_is_frozen():
    """v0.7 schema file stays parseable and pinned to const "0.7".
    Catches accidental edits or regeneration overwrites of frozen
    schemas."""
    schema = json.loads(REPORT_SCHEMA_V07.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.7"}
    assert "release_decision" not in schema.get("required", [])


def test_v08_schema_file_is_frozen():
    """v0.8 schema file stays parseable and excludes v0.9 additive fields."""
    schema = json.loads(REPORT_SCHEMA_V08.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.8"}
    for field in (
        "capability_facts",
        "declared_intentions",
        "misalignments",
        "release_consequence",
        "suggested_scenarios",
    ):
        assert field not in schema.get("required", [])


def test_v09_schema_file_is_frozen():
    """v0.9 schema file stays parseable and excludes v0.10 additive fields."""
    schema = json.loads(REPORT_SCHEMA_V09.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.9"}
    for field in ("tool_surface_facts", "tool_surface_diff"):
        assert field not in schema.get("required", [])


def test_v10_schema_file_is_frozen():
    """v0.10 schema file stays parseable and pinned to const "0.10"."""
    schema = json.loads(REPORT_SCHEMA_V10.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.10"}


def test_v11_schema_file_is_frozen():
    """v0.11 schema file stays parseable and pinned to const "0.11".
    Excludes v0.12 additive fields (agent_action / agent_summary)."""
    schema = json.loads(REPORT_SCHEMA_V11.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.11"}
    assert "agent_summary" not in schema.get("required", [])
    assert "agent_summary" not in schema.get("properties", {})
    finding_props = schema.get("$defs", {}).get("Finding", {}).get("properties", {})
    assert "agent_action" not in finding_props, (
        "v0.11 schema must not declare agent_action; it ships in v0.12."
    )


def test_v12_schema_file_is_frozen():
    """v0.12 schema file stays parseable and excludes v0.13 additive fields."""
    schema = json.loads(REPORT_SCHEMA_V12.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.12"}
    assert "codex_plugin_surface" not in schema.get("required", [])
    assert "codex_plugin_surface" not in schema.get("properties", {})


def test_v13_schema_file_is_frozen():
    """v0.13 schema file stays parseable and pinned to const "0.13".
    The release_decision.decision enum must remain at the v0.13 three
    values; insufficient_evidence ships in v0.14."""
    schema = json.loads(REPORT_SCHEMA_V13.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.13"}
    rd_decision = (
        schema["$defs"]["ReleaseDecision"]["properties"]["decision"]
    )
    assert set(rd_decision["enum"]) == {"blocked", "review_required", "passed"}
    summary_verdict = (
        schema["$defs"]["AgentSummary"]["properties"]["verdict"]
    )
    assert set(summary_verdict["enum"]) == {
        "blocked",
        "review_required",
        "passed",
    }


def test_v14_schema_file_is_frozen():
    """v0.14 schema file stays parseable and pinned to const "0.14".
    v0.14 adds insufficient_evidence to release_decision.decision and
    agent_summary.verdict; provenance_kind ships in v0.15."""
    schema = json.loads(REPORT_SCHEMA_V14.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.14"}
    rd_decision = (
        schema["$defs"]["ReleaseDecision"]["properties"]["decision"]
    )
    assert set(rd_decision["enum"]) == {
        "blocked",
        "review_required",
        "insufficient_evidence",
        "passed",
    }
    summary_verdict = (
        schema["$defs"]["AgentSummary"]["properties"]["verdict"]
    )
    assert set(summary_verdict["enum"]) == {
        "blocked",
        "review_required",
        "insufficient_evidence",
        "passed",
    }


def test_v15_schema_file_is_frozen():
    """v0.15 schema file stays parseable and excludes v0.16 action fields."""
    schema = json.loads(REPORT_SCHEMA_V15.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.15"}
    assert "action_surface_facts" not in schema.get("required", [])
    assert "action_surface_diff" not in schema.get("required", [])
    assert "action_surface_facts" not in schema.get("properties", {})
    assert "action_surface_diff" not in schema.get("properties", {})


def test_v07_schema_preserves_nested_required_lists():
    """Top-level required fields plus nested required lists for Finding,
    tool_inventory[], loaded_plugins[], LoadedPolicyPack, and per-framework
    surfaces must mirror the v0.5 contract. Optional v0.7 additions
    (Finding.patches, manifest_dir, and the four remediation fields)
    are NOT added to required — they remain optional for additive
    consumers.

    Regression for v0.6 reviewer feedback: Pydantic auto-generation
    weakens nested requireds because most fields have defaults.
    """
    schema = json.loads(REPORT_SCHEMA_V07.read_text(encoding="utf-8"))

    finding_required = set(schema["$defs"]["Finding"]["required"])
    assert finding_required >= {
        "id",
        "fingerprint",
        "check_id",
        "title",
        "severity",
        "category",
        "evidence",
        "confidence",
        "recommendation",
        "suppressed",
        "baseline_status",
    }
    # patches and v0.7 additions stay optional (additive).
    assert "patches" not in finding_required
    for new_field in (
        "autofix_safe",
        "requires_human_review",
        "suggested_patch_kind",
        "docs_url",
    ):
        assert new_field not in finding_required, (
            f"v0.7 added {new_field} as optional; must not appear in required"
        )

    tool_inventory_required = set(
        schema["properties"]["tool_inventory"]["items"]["required"]
    )
    assert tool_inventory_required == {
        "name",
        "source_type",
        "risk_tags",
        "auth_scopes",
        "confidence",
    }
    loaded_plugins_required = set(
        schema["properties"]["loaded_plugins"]["items"]["required"]
    )
    assert loaded_plugins_required == {
        "name",
        "value",
        "distribution",
        "version",
        "check_id",
    }
    loaded_pack_required = set(schema["$defs"]["LoadedPolicyPack"]["required"])
    assert loaded_pack_required == {"id", "name", "path", "rule_count"}

    google_adk_required = set(
        schema["properties"]["frameworks"]["properties"]["google_adk"]["required"]
    )
    assert "agent_count" in google_adk_required
    assert "dynamic_toolset_count" in google_adk_required


def test_v17_loaded_plugins_required_includes_validation_fields():
    """v0.17 (M5): plugin validation provenance fields are required at
    the JSON-schema level on every emitted ``loaded_plugins[]`` entry.

    Paired with ``test_v07_schema_preserves_nested_required_lists``
    above, which locks the original 5-field contract for the frozen
    v0.7 schema. Generator wiring lives in
    ``scripts/generate_schemas.py::_postprocess_report`` near the
    ``loaded_plugins`` block.
    """

    schema = json.loads(REPORT_SCHEMA_V17.read_text(encoding="utf-8"))
    required = set(schema["properties"]["loaded_plugins"]["items"]["required"])
    assert required == {
        "name",
        "value",
        "distribution",
        "version",
        "check_id",
        "validation_status",
        "validation_errors",
        "runtime_errors",
    }


def test_v08_schema_requires_release_decision():
    """Top-level required must include `release_decision` and the
    ReleaseDecision $def must require all leaf blocks. Catches drift
    between the model and the published v0.8 contract."""
    schema = json.loads(REPORT_SCHEMA_V08.read_text(encoding="utf-8"))
    assert "release_decision" in schema["required"]
    assert schema["properties"]["report_schema_version"] == {"const": "0.8"}
    # The Pydantic model declares `release_decision: ReleaseDecision | None`
    # for test-helper convenience, but the published schema must NOT allow
    # null — every emitted v0.8 report has a populated release_decision.
    assert schema["properties"]["release_decision"] == {
        "$ref": "#/$defs/ReleaseDecision"
    }

    decision_required = set(schema["$defs"]["ReleaseDecision"]["required"])
    assert decision_required == {
        "decision",
        "reason",
        "blockers",
        "review_items",
        "evidence_coverage",
        "baseline_delta",
        "fail_policy",
    }
    fail_policy_required = set(schema["$defs"]["FailPolicy"]["required"])
    assert fail_policy_required == {
        "ci_mode",
        "fail_on",
        "new_findings_only",
        "would_fail_ci",
        "exit_code",
    }
    evidence_required = set(
        schema["$defs"]["EvidenceCoverageDecision"]["required"]
    )
    assert evidence_required == {
        "level",
        "human_review_recommended",
        "source_warning_count",
        "low_confidence_tool_count",
    }
    # STABILITY.md guarantees the full v0.8 contract on each item: id,
    # fingerprint, check_id, severity, title, baseline_status. The
    # nullable ones (id/fingerprint/baseline_status) must still appear
    # as keys so consumers can read them without conditional checks.
    item_required = set(schema["$defs"]["ReleaseDecisionItem"]["required"])
    assert item_required == {
        "id",
        "fingerprint",
        "check_id",
        "severity",
        "title",
        "baseline_status",
    }


def test_v10_schema_requires_release_decision_and_diffs():
    """Top-level required must include release_decision and additive diff fields."""
    schema = json.loads(REPORT_SCHEMA_V10.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.10"}
    assert {
        "release_decision",
        "capability_facts",
        "declared_intentions",
        "misalignments",
        "release_consequence",
        "suggested_scenarios",
        "tool_surface_facts",
        "tool_surface_diff",
    } <= set(schema["required"])
    assert schema["properties"]["release_decision"] == {
        "$ref": "#/$defs/ReleaseDecision"
    }
    assert schema["properties"]["release_consequence"] == {
        "$ref": "#/$defs/ReleaseConsequence"
    }

    capability_required = set(schema["$defs"]["CapabilityFact"]["required"])
    assert capability_required == {
        "id",
        "tool_name",
        "source_type",
        "source_ref",
        "capability",
        "risk_tags",
        "auth_scopes",
        "owner",
        "included_reason",
        "control_status",
        "related_findings",
    }
    assert schema["$defs"]["CapabilityFact"]["properties"]["included_reason"]["enum"] == [
        "high_risk_tag",
        "wildcard_exposure",
        "referenced_by_critical_finding",
        "referenced_by_high_finding",
        "referenced_by_medium_finding",
    ]
    assert schema["$defs"]["Misalignment"]["properties"]["kind"]["enum"] == [
        "policy_gap",
        "scope_drift",
        "prohibited_action_present",
        "control_missing",
        "intent_mismatch",
        "undetected_gap",
    ]
    assert schema["$defs"]["SuggestedScenario"]["properties"]["scenario_type"]["enum"] == [
        "approval",
        "confirmation",
        "idempotency_retry",
        "least_privilege_scope",
        "prohibited_action",
        "wildcard_inventory",
        "schema_boundary",
        "prompt_scope_alignment",
        "test_case_coverage",
    ]
    diff_required = set(schema["$defs"]["ToolSurfaceDiff"]["required"])
    assert {
        "enabled",
        "base",
        "summary",
        "tools",
        "high_risk_effects",
        "scopes",
        "controls",
        "metadata_changes",
        "policy_drift",
        "finding_deltas",
        "notes",
    } <= diff_required


def test_v17_schema_requires_contribution_rules():
    """v0.17 adds release_decision.contribution_rules[] — a deterministic
    per-finding audit of how each finding contributed to the decision.
    The field is required + always present (defaults to []) so consumers
    never need an existence check. Locks the v0.17 contract; M8 of the
    trust-hardening pass."""
    schema = json.loads(REPORT_SCHEMA_V17.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.17"}
    # ReleaseDecision still requires the v0.8 baseline of fields plus
    # the v0.17 audit field.
    release_decision_required = set(schema["$defs"]["ReleaseDecision"]["required"])
    assert "contribution_rules" in release_decision_required
    # Every prior required key is preserved.
    assert {
        "decision",
        "reason",
        "blockers",
        "review_items",
        "evidence_coverage",
        "baseline_delta",
        "fail_policy",
    } <= release_decision_required
    # ContributionRule shape is pinned: every emitted row carries
    # finding_id / fingerprint(key, may be null) / check_id / category /
    # rule / rationale.
    contrib_def = schema["$defs"]["ContributionRule"]
    assert set(contrib_def["required"]) == {
        "finding_id",
        "fingerprint",
        "check_id",
        "category",
        "rule",
        "rationale",
    }
    # Both the rule enum and the category enum are inlined; agents that
    # switch on the audit need to know the closed grammar.
    rule_enum = set(contrib_def["properties"]["rule"]["enum"])
    assert rule_enum == {
        "policy_block_new",
        "severity_block_new",
        "policy_baseline_accepted",
        "severity_baseline_accepted",
        "review_required",
        "sub_threshold",
        "suppressed",
    }
    category_enum = set(contrib_def["properties"]["category"]["enum"])
    assert category_enum == {"blocker", "review_item", "excluded"}


def test_v18_schema_requires_privacy_audit():
    """v0.18 adds the default redaction audit envelope."""
    schema = json.loads(REPORT_SCHEMA_V18.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.18"}
    assert "privacy_audit" in set(schema["required"])
    assert schema["properties"]["privacy_audit"] == {"$ref": "#/$defs/PrivacyAudit"}
    audit_def = schema["$defs"]["PrivacyAudit"]
    assert {
        "enabled",
        "rules_version",
        "sensitive_field_inventory_version",
        "redacted_occurrence_count",
        "redacted_paths",
        "output_surfaces",
        "notes",
    } <= set(audit_def["required"])
    path_def = schema["$defs"]["RedactedPathSummary"]
    assert {"path", "count", "kinds"} <= set(path_def["required"])


def test_v16_schema_requires_action_surface_fields():
    """v0.16 adds first-class Action Surface Diff facts and diff fields."""
    schema = json.loads(REPORT_SCHEMA_V16.read_text(encoding="utf-8"))
    assert schema["properties"]["report_schema_version"] == {"const": "0.16"}
    assert {
        "action_surface_facts",
        "action_surface_diff",
    } <= set(schema["required"])
    assert {
        "enabled",
        "base",
        "summary",
        "added",
        "removed",
        "modified",
        "notes",
    } <= set(schema["$defs"]["ActionSurfaceDiff"]["required"])
    assert {
        "actions_added",
        "actions_removed",
        "actions_modified",
        "blocking_findings",
    } <= set(schema["$defs"]["ActionSurfaceDiffSummary"]["required"])
    assert "blocks_release" in schema["$defs"]["Finding"]["required"]
    assert "blocks_release" in schema["$defs"]["ReleaseDecisionItem"]["required"]


def test_current_schema_rejects_null_release_decision_and_consequence(tmp_path):
    """A current payload with null release blocks MUST fail validation.
    Regression for the original schema which emitted
    `anyOf: [ReleaseDecision, null]` and silently accepted null. Invariant
    carries forward unchanged from v0.13/v0.14/v0.15/v0.16."""
    import jsonschema

    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    schema = json.loads(REPORT_SCHEMA_CURRENT.read_text(encoding="utf-8"))
    payload = report_json_payload(report)

    # Sanity: real payload validates.
    validate(instance=payload, schema=schema)

    # Tamper: setting release_decision to null must be rejected.
    payload["release_decision"] = None
    with pytest.raises(jsonschema.ValidationError):
        validate(instance=payload, schema=schema)

    payload = report_json_payload(report)
    payload["release_consequence"] = None
    with pytest.raises(jsonschema.ValidationError):
        validate(instance=payload, schema=schema)


def test_json_report_omits_patches_key_when_not_suggested(tmp_path):
    """Per C4: scan without --suggest-patches must NOT include the
    `patches` key on any finding. Run-id stability for non-opting
    callers depends on this."""
    from agents_shipgate.report.json_report import report_json_payload

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
    )
    payload = report_json_payload(report)
    for finding in payload["findings"]:
        assert "patches" not in finding


def test_markdown_escapes_user_controlled_tool_metadata(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Injection
  version: "1.0"
paths:
  /records:
    post:
      operationId: "[Click here](https://evil.example)"
      summary: "Update [records](https://evil.example)"
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                updates:
                  type: object
      responses:
        "200":
          description: ok
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: "**bold** _team_ <tag>"
agent:
  name: markdown-agent
  declared_purpose:
    - update records
environment:
  target: local
tool_sources:
  - id: api
    type: openapi
    path: openapi.yaml
policies:
  require_approval_for_tools:
    - "[Click here](https://evil.example)"
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )
    markdown = render_markdown_report(report)

    assert "[Click here](https://evil.example)" not in markdown
    assert "\\[Click here\\]\\(https://evil.example\\)" in markdown
    assert "**bold** _team_ <tag>" not in markdown
    assert "\\*\\*bold\\*\\* \\_team\\_ \\<tag\\>" in markdown
    assert _safe_markdown_text("**bold** _underscore_ <tag>") == (
        "\\*\\*bold\\*\\* \\_underscore\\_ \\<tag\\>"
    )


def test_clean_report_has_affirmative_pass_result(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.lookup",
      "description": "Look up internal documentation metadata for an existing support article.",
      "annotations": {"readOnlyHint": true}
    }
  ]
}
""",
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: clean-test
agent:
  name: clean-agent
  declared_purpose:
    - look up documentation metadata
environment:
  target: local
tool_sources:
  - id: docs
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - {tool: docs.lookup, source_id: docs}
      handoffs: []
      reason: reviewed clean fixture binding
action_surface:
  actions:
    - tool: docs.lookup
      effect: read
      authority:
        mode: none
""",
        encoding="utf-8",
    )

    report, _ = run_scan(
        config_path=project / "shipgate.yaml",
        output_dir=tmp_path / "reports",
        formats=["json"],
        ci_mode="advisory",
    )

    # v0.8: the legacy "Result: PASS ..." line was removed in favor of
    # the leading Release Decision block. A clean scan with high-confidence
    # tools yields decision=passed.
    markdown = render_markdown_report(report)
    assert "## Release Decision" in markdown
    assert "Decision: passed" in markdown
    assert "## Capability <-> Intent Diff" in markdown
    assert "No capability/intent misalignments detected from static evidence." in markdown


def test_preserved_evidence_findings_cite_the_artifact_that_declares_it(tmp_path):
    """#386 follow-up: a backfilled value must not be reported against the primary.

    `send_email_preview(...) -> str` is bound to a reviewed inventory that
    declares no output schema. Restoring the erased schema restored the
    free-form-output finding — but the canonical row keeps the *inventory's*
    locators, so the finding cited `inventories/sdk-tools.json#/tools/0`, an
    object with no output schema in it at all. The reviewer needs the artifact
    that actually supports the evidence.
    """

    report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path,
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    freeform = [
        finding
        for finding in report.findings
        if finding.check_id == "SHIP-SCHEMA-FREEFORM-OUTPUT"
    ]
    assert freeform, "expected the restored free-form-output finding"
    (finding,) = freeform
    assert finding.source.ref == "agents/refund_agent.py"
    assert finding.source.location == "agents/refund_agent.py:5"
    supplied_by = finding.evidence["evidence_supplied_by"]
    assert supplied_by["field"] == "output_schema"
    assert supplied_by["source_ref"] == "agents/refund_agent.py"
