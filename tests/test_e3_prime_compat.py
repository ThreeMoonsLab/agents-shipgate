from __future__ import annotations


def test_cli_scan_package_preserves_public_imports() -> None:
    from agents_shipgate.cli.scan import (
        _build_agent,
        _flatten_and_deduplicate_tools,
        _load_sources,
        _resolve_audit_log_path,
        _run_id,
        inspect_sources,
        run_scan,
    )

    assert callable(run_scan)
    assert callable(inspect_sources)
    assert callable(_load_sources)
    assert callable(_flatten_and_deduplicate_tools)
    assert callable(_build_agent)
    assert callable(_run_id)
    assert callable(_resolve_audit_log_path)


def test_core_findings_package_preserves_public_imports() -> None:
    from agents_shipgate.core.findings import (
        _REMEDIATION_FALLBACK,
        SEVERITY_ORDER,
        _canonicalize_for_fingerprint,
        annotate_remediation,
        apply_severity_overrides,
        apply_suppressions,
        assign_finding_ids,
        build_agent_summary,
        build_report,
        build_reviewer_summary,
        dedupe_findings,
        derive_agent_action,
        finding_fingerprint,
        summarize_findings,
    )

    assert SEVERITY_ORDER["critical"] == 0
    assert _REMEDIATION_FALLBACK["suggested_patch_kind"] == "manual"
    assert callable(assign_finding_ids)
    assert callable(dedupe_findings)
    assert callable(apply_severity_overrides)
    assert callable(apply_suppressions)
    assert callable(annotate_remediation)
    assert callable(derive_agent_action)
    assert callable(build_agent_summary)
    assert callable(build_report)
    assert callable(build_reviewer_summary)
    assert callable(summarize_findings)
    assert callable(finding_fingerprint)
    assert callable(_canonicalize_for_fingerprint)
