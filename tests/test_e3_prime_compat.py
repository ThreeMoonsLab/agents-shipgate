"""Lock the public re-export surface of ``cli/scan`` and ``core/findings``.

The original E3-prime decomposition (monolithic ``cli/scan.py`` → nine
phase modules under ``cli/scan/``) preserved every private helper as a
package-level re-export for backwards compatibility. The round-3
architecture review flagged this as the wrong contract: phase helpers
are implementation detail and should be reachable only through the
owning submodule.

These tests now lock the *new* public surface:

- ``cli/scan`` exposes ``run_scan``, ``inspect_sources``, and
  ``PACKET_FORMAT_NAMES``. Phase helpers must be imported from their
  owning submodule (e.g. ``cli.scan.run_identity._run_id``).
- ``core/findings`` continues to expose the public computation helpers
  used by external callers — this contract was already documented and
  remains unchanged.
"""

from __future__ import annotations


def test_cli_scan_package_public_surface_is_minimal() -> None:
    """``cli/scan`` re-exports only the public surface.

    Phase helpers stay reachable through their owning submodule, but
    are intentionally NOT re-exported at the package level. Adding a
    new re-export here is a deliberate API decision — do not relax
    this test without architectural review.
    """

    import agents_shipgate.cli.scan as scan_pkg

    assert set(scan_pkg.__all__) == {
        "PACKET_FORMAT_NAMES",
        "inspect_sources",
        "run_scan",
    }
    assert callable(scan_pkg.run_scan)
    assert callable(scan_pkg.inspect_sources)
    assert isinstance(scan_pkg.PACKET_FORMAT_NAMES, (tuple, frozenset, set, list))


def test_cli_scan_phase_helpers_reachable_via_submodule() -> None:
    """Phase helpers are still importable — just not from the package root.

    This is the canonical replacement for the legacy E3-prime
    compatibility test. Each helper is reachable from its owning
    submodule; the package root no longer re-exports it.
    """

    from agents_shipgate.cli.scan.agent_builder import _build_agent
    from agents_shipgate.cli.scan.path_helpers import _resolve_audit_log_path
    from agents_shipgate.cli.scan.run_identity import _run_id
    from agents_shipgate.cli.scan.source_loading import (
        _flatten_and_deduplicate_tools,
        _load_sources,
    )

    assert callable(_build_agent)
    assert callable(_flatten_and_deduplicate_tools)
    assert callable(_load_sources)
    assert callable(_resolve_audit_log_path)
    assert callable(_run_id)


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
