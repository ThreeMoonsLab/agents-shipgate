"""Tier B SHIP-VERIFY-* trust-root weakening checks (roadmap §5.1/§5.3).

Covers the five semantic-weakening checks added in M3:

- SHIP-VERIFY-POLICY-WEAKENED
- SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED
- SHIP-VERIFY-CI-GATE-REMOVED
- SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED
- SHIP-VERIFY-TRIGGER-CATALOG-DRIFT

Plus the normalized effective-policy snapshot they compare against. Each
check must: (a) emit nothing without a VerificationContext, (b) compare
base-vs-head via the snapshot (not a text diff), (c) fail safe to
review_required when direction can't be proven, and (d) emit ordinary
category-"verify" findings that flow through the one decision engine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents_shipgate.checks import (
    verify_agent_instructions,
    verify_baseline_waiver,
    verify_ci_gate,
    verify_policy,
    verify_trigger_drift,
)
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.core.lenses.effective_policy import (
    build_effective_policy_snapshot,
)
from agents_shipgate.core.lenses.tool_surface import ToolSurfaceDiffReference
from agents_shipgate.core.trust_roots import trust_root_class_for
from agents_shipgate.schemas.capability_change import EffectivePolicy
from agents_shipgate.schemas.verification import VerificationContext

SAMPLE = Path("samples/support_refund_agent/shipgate.yaml")


def _manifest():
    return load_manifest(SAMPLE)


def _context(
    *,
    changed_files: list[str] | None = None,
    verification: bool = True,
    base_policy: EffectivePolicy | None = None,
    manifest=None,
    config_path: str = "shipgate.yaml",
) -> ScanContext:
    vc = (
        VerificationContext(changed_files=changed_files or [])
        if verification
        else None
    )
    diff_reference = (
        ToolSurfaceDiffReference(kind="report", facts=None, effective_policy=base_policy)
        if base_policy is not None
        else None
    )
    return ScanContext(
        manifest=manifest or _manifest(),
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path(config_path),
        verification=vc,
        diff_reference=diff_reference,
    )


# --- effective-policy snapshot ---------------------------------------------


def test_snapshot_is_deterministic_and_normalized():
    manifest = _manifest()
    a = build_effective_policy_snapshot(manifest)
    b = build_effective_policy_snapshot(manifest)
    assert a.model_dump() == b.model_dump()
    # fail_on is tier-sorted, lists are sorted+unique.
    assert a.fail_on == sorted(set(a.fail_on), key=_rank)
    assert a.suppressed_check_ids == sorted(set(a.suppressed_check_ids))


def _rank(sev):
    from agents_shipgate.schemas.capability_change import SEVERITY_RANK

    return (SEVERITY_RANK.get(sev, -1), sev)


def test_cli_overrides_never_reach_the_effective_policy_snapshot(tmp_path):
    """The snapshot describes the repository, never the invocation (#298).

    This is the structural guard behind the end-to-end regressions further
    down: it drives every CLI override ``_prepare_scan`` supports at once
    and asserts the snapshot is byte-identical to one built from the
    untouched on-disk manifest. A future override that lands on a
    snapshot field without carrying its declared value fails here.
    """
    from agents_shipgate.cli.scan.prepare import _prepare_scan

    resolved = _prepare_scan(
        config_path=SAMPLE,
        ci_mode="strict",
        fail_on=["critical"],
        output_dir=tmp_path,
        formats=["json"],
        packet_enabled=False,
        packet_formats=["md"],
        baseline_mode="new-findings",
    )
    # The run itself honors the overrides — that is what gates this scan.
    assert resolved.manifest.ci.mode == "strict"
    assert list(resolved.manifest.ci.fail_on) == ["critical"]
    # The snapshot does not.
    assert build_effective_policy_snapshot(
        resolved.manifest, declared_ci=resolved.declared_ci
    ).model_dump() == build_effective_policy_snapshot(_manifest()).model_dump()


# --- SHIP-VERIFY-POLICY-WEAKENED -------------------------------------------


def test_policy_weakened_no_verification_emits_nothing():
    assert verify_policy.run(_context(verification=False)) == []


def test_policy_weakened_ci_mode_strict_to_advisory():
    base = EffectivePolicy(ci_mode="strict", fail_on=["high", "critical"])
    ctx = _context(base_policy=base)
    # Head manifest (support_refund) is advisory by default -> weakened.
    findings = verify_policy.run(ctx)
    kinds = {f.evidence.get("kind") for f in findings}
    assert "ci_mode_weakened" in kinds
    assert all(f.check_id == "SHIP-VERIFY-POLICY-WEAKENED" for f in findings)
    assert all(f.category == "verify" for f in findings)
    assert all(not f.blocks_release for f in findings)


def test_policy_weakened_fail_on_loosened():
    head = build_effective_policy_snapshot(_manifest())
    # Base required an extra severity that head no longer fails on.
    base = EffectivePolicy(
        ci_mode=head.ci_mode,
        fail_on=sorted(set(head.fail_on) | {"critical", "high", "medium"}),
    )
    findings = verify_policy.run(_context(base_policy=base))
    kinds = {f.evidence.get("kind") for f in findings}
    assert "fail_on_loosened" in kinds


def test_policy_weakened_severity_override_lowered():
    head = build_effective_policy_snapshot(_manifest())
    base = EffectivePolicy(
        ci_mode=head.ci_mode,
        fail_on=head.fail_on,
        severity_overrides={"SHIP-DOC-MISSING-DESCRIPTION": "critical"},
    )
    findings = verify_policy.run(_context(base_policy=base))
    kinds = {f.evidence.get("kind") for f in findings}
    assert "severity_override_lowered" in kinds
    lowered = [
        f for f in findings if f.evidence.get("kind") == "severity_override_lowered"
    ]
    assert lowered[0].evidence["base_severity"] == "critical"
    assert lowered[0].evidence["head_severity"] == "medium"


def test_policy_weakened_new_downgrade_override_uses_catalog_default():
    from agents_shipgate.schemas.manifest import SeverityOverrideEntry

    manifest = _manifest()
    manifest.checks.severity_overrides["SHIP-AUTH-MANIFEST-BROAD-SCOPE"] = (
        SeverityOverrideEntry(severity="medium")
    )
    head = build_effective_policy_snapshot(manifest)
    base = EffectivePolicy(ci_mode=head.ci_mode, fail_on=head.fail_on)
    findings = verify_policy.run(_context(base_policy=base, manifest=manifest))
    lowered = [
        f for f in findings if f.evidence.get("kind") == "severity_override_lowered"
    ]
    assert lowered
    assert lowered[0].evidence["target_check_id"] == "SHIP-AUTH-MANIFEST-BROAD-SCOPE"
    assert lowered[0].evidence["base_severity"] == "high"
    assert lowered[0].evidence["head_severity"] == "medium"


def test_policy_weakened_strengthening_emits_nothing():
    # Base was WEAKER (advisory + smaller fail_on); the head sample manifest
    # is same-or-stronger, so no weakening kind should fire.
    base = EffectivePolicy(ci_mode="advisory", fail_on=[])
    findings = verify_policy.run(_context(base_policy=base))
    # No weakening kinds (ci same, fail_on grew or equal).
    kinds = {f.evidence.get("kind") for f in findings}
    assert "ci_mode_weakened" not in kinds
    assert "fail_on_loosened" not in kinds


def test_policy_weakened_no_base_but_policy_touched_fails_safe():
    # No base snapshot, but the PR changed shipgate.yaml -> review-required.
    findings = verify_policy.run(
        _context(base_policy=None, changed_files=["shipgate.yaml"])
    )
    assert len(findings) == 1
    assert findings[0].evidence["kind"] == "base_snapshot_unavailable"
    assert findings[0].severity == "medium"


def test_policy_weakened_no_base_no_policy_touch_emits_nothing():
    findings = verify_policy.run(
        _context(base_policy=None, changed_files=["README.md"])
    )
    assert findings == []


# --- SHIP-VERIFY-POLICY-WEAKENED through the real `verify --base` path ------
#
# The unit tests above inject the base ``EffectivePolicy`` directly, so they
# cannot see how the snapshot is *produced*. These two drive real base and
# head scans through ``run_verify``, which is where the snapshot used to
# describe the invocation instead of the repository (issue #298): the base
# scan is forced to ``ci_mode="advisory"`` and the head scan inherits the
# caller's ``--ci-mode`` / ``--fail-on``. Both directions are covered — the
# under-fire on ``ci_mode`` and the over-fire on ``fail_on``.


def _init_repo(repo: Path) -> None:
    import subprocess

    for args in (
        ("init",),
        ("config", "user.email", "test@example.test"),
        ("config", "user.name", "Test User"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _commit(repo: Path, message: str) -> None:
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True
    )


def _sample_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the sample agent into a fresh git repo. Returns (repo, manifest)."""
    import shutil

    repo = tmp_path / "repo"
    sample_dst = repo / "samples" / "support_refund_agent"
    sample_dst.parent.mkdir(parents=True)
    shutil.copytree(Path(__file__).resolve().parent.parent / SAMPLE.parent, sample_dst)
    return repo, sample_dst / "shipgate.yaml"


def _weakened_repo(tmp_path: Path) -> Path:
    """A repo whose HEAD downgrades the declared gate strict -> advisory."""
    repo, manifest_path = _sample_repo(tmp_path)
    declared = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        declared.replace("ci:\n  mode: advisory", "ci:\n  mode: strict"),
        encoding="utf-8",
    )
    _init_repo(repo)
    _commit(repo, "base: strict gate")
    manifest_path.write_text(declared, encoding="utf-8")
    _commit(repo, "head: weaken gate to advisory")
    return repo


def _run_verify(repo: Path, **overrides):
    from agents_shipgate.cli.verify.orchestrator import run_verify

    kwargs = dict(
        workspace=repo,
        config=Path("samples/support_refund_agent/shipgate.yaml"),
        base="HEAD~1",
        head="HEAD",
        archive_head=True,
        out=repo / "agents-shipgate-reports",
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
    )
    kwargs.update(overrides)
    return run_verify(**kwargs)


def _policy_kinds(report) -> set[str]:
    return {
        f.evidence.get("kind")
        for f in report.findings
        if f.check_id == "SHIP-VERIFY-POLICY-WEAKENED"
    }


def test_ci_mode_weakened_fires_through_real_base_scan(tmp_path):
    """strict -> advisory in the manifest must be named by the specific check.

    Regression for #298: the base scan runs with a forced
    ``ci_mode="advisory"`` for its own exit code, and that override used to
    leak into the base report's ``effective_policy``, so every base read back
    as advisory and this disjunct could never fire.
    """
    repo = _weakened_repo(tmp_path)

    _, report, _ = _run_verify(repo)
    assert report is not None
    assert "ci_mode_weakened" in _policy_kinds(report)
    weakened = next(
        f
        for f in report.findings
        if f.evidence.get("kind") == "ci_mode_weakened"
    )
    assert weakened.evidence["base_ci_mode"] == "strict"
    assert weakened.evidence["head_ci_mode"] == "advisory"


def test_fail_on_cli_override_is_not_reported_as_loosening(tmp_path):
    """``--fail-on`` narrows this run; it does not weaken the repository.

    Regression for #298: the head scan applied the caller's ``--fail-on``
    to the manifest before the snapshot was taken while the base scan did
    not, so a PR that touched no policy at all was accused of dropping a
    severity from the gate.
    """
    repo, manifest_path = _sample_repo(tmp_path)
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "ci:\n  mode: advisory",
            "ci:\n  mode: advisory\n  fail_on: [high, critical]",
        ),
        encoding="utf-8",
    )
    _init_repo(repo)
    _commit(repo, "base")
    (repo / "README.md").write_text("unrelated\n", encoding="utf-8")
    _commit(repo, "head: unrelated change")

    _, report, _ = _run_verify(repo, fail_on=["high"])
    assert report is not None
    assert "fail_on_loosened" not in _policy_kinds(report)


def test_ci_mode_weakening_still_fires_under_a_strict_cli_override(tmp_path):
    """A ``--ci-mode strict`` invocation must not mask a weakened manifest."""
    repo = _weakened_repo(tmp_path)

    _, report, _ = _run_verify(repo, ci_mode="strict")
    assert report is not None
    assert "ci_mode_weakened" in _policy_kinds(report)


def _stale_base_cache_entries(repo: Path) -> list[Path]:
    """Rewrite every cached base report the way the pre-#298 CLI left it.

    ``ci_mode`` back to ``"advisory"`` with a matching content hash — the
    cache admits an entry on that hash alone, so this is indistinguishable
    from an entry a pre-fix build wrote.
    """
    import hashlib

    entries = sorted(
        (repo / ".git" / "agents-shipgate" / "base-scans").glob("*/report.json")
    )
    for entry in entries:
        payload = json.loads(entry.read_text(encoding="utf-8"))
        payload["effective_policy"]["ci_mode"] = "advisory"
        entry.write_text(json.dumps(payload), encoding="utf-8")
        entry.with_suffix(".sha256").write_text(
            f"{hashlib.sha256(entry.read_bytes()).hexdigest()}\n", encoding="ascii"
        )
    return entries


# The base-scan cache-key epoch the pre-#298 CLI wrote its entries under.
# Pinned as a literal, not as ``BASE_CACHE_KEY_EPOCH - 1``: the point is that
# entries from THIS epoch stay unreachable, which a relative expression would
# stop asserting the moment the epoch moved again — or if it were reverted.
_PRE_298_CACHE_EPOCH = 2


def test_base_cache_from_the_pre_fix_epoch_is_not_reused(tmp_path, monkeypatch):
    """Fixing the producer is not enough while stale entries stay readable.

    The base-scan cache validates an entry by content hash, which proves it
    was not tampered with and says nothing about whether its fields still
    mean what this CLI expects. ``__version__`` is in the key but does not
    move for a source checkout or between two builds sharing a pre-release
    version string, so a base report written before #298 — recording
    ``advisory`` for a base that declared ``strict`` — was reused verbatim
    and the fix stayed invisible. ``BASE_CACHE_KEY_EPOCH`` strands those
    entries on a key nothing computes.
    """
    from agents_shipgate.cli.verify import orchestrator as verify_orchestrator

    repo = _weakened_repo(tmp_path)
    monkeypatch.setattr(
        verify_orchestrator, "BASE_CACHE_KEY_EPOCH", _PRE_298_CACHE_EPOCH
    )
    _run_verify(repo)
    assert _stale_base_cache_entries(repo), "no base cache entry was written to poison"

    # Positive control: under the epoch that wrote it, the poisoned entry IS
    # believed. Without this the assertion below could pass because the entry
    # was never readable in the first place.
    _, report, _ = _run_verify(repo)
    assert report is not None
    assert "ci_mode_weakened" not in _policy_kinds(report)

    # Under the shipped epoch the same entry is unreachable, so the base is
    # re-scanned and the weakening is named.
    monkeypatch.undo()
    _, report, _ = _run_verify(repo)
    assert report is not None
    assert "ci_mode_weakened" in _policy_kinds(report)


# --- SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED -------------------------------


def test_waiver_suppression_expanded():
    head = build_effective_policy_snapshot(_manifest())
    # Base had FEWER suppressions than head. Inject a head suppression by
    # comparing against an empty base.
    base = EffectivePolicy(
        ci_mode=head.ci_mode,
        fail_on=head.fail_on,
        suppressed_check_ids=[],
        waiver_scopes=[],
    )
    if not head.suppressed_check_ids:
        # The sample has no suppressions; simulate one by swapping base/head
        # roles via a synthetic head with a suppression.
        base = EffectivePolicy(suppressed_check_ids=[], waiver_scopes=[])
        ctx = _context(base_policy=base)
        # Patch the manifest's ignore list through a fresh snapshot.
        from agents_shipgate.schemas.manifest import SuppressionConfig

        ctx.manifest.checks.ignore.append(
            SuppressionConfig(check_id="SHIP-DOC-MISSING-DESCRIPTION", reason="x")
        )
        findings = verify_baseline_waiver.run(ctx)
        kinds = {f.evidence.get("kind") for f in findings}
        assert "suppression_expanded" in kinds
        assert any(
            "SHIP-DOC-MISSING-DESCRIPTION" in f.evidence.get("added_check_ids", [])
            for f in findings
        )


def test_waiver_no_base_emits_nothing():
    # Touching baseline files without a base is covered by TRUST-ROOT-TOUCHED;
    # the expansion check needs the base and emits nothing without it.
    findings = verify_baseline_waiver.run(
        _context(base_policy=None, changed_files=[".agents-shipgate/baseline.json"])
    )
    assert findings == []


def test_waiver_no_expansion_emits_nothing():
    head = build_effective_policy_snapshot(_manifest())
    base = EffectivePolicy(
        ci_mode=head.ci_mode,
        fail_on=head.fail_on,
        suppressed_check_ids=head.suppressed_check_ids,
        waiver_scopes=head.waiver_scopes,
        baseline_fingerprints=[],
    )
    findings = verify_baseline_waiver.run(_context(base_policy=base))
    assert findings == []


def test_baseline_expansion_compares_after_baseline_matching(tmp_path):
    from agents_shipgate.cli.scan import run_scan
    from agents_shipgate.core.baseline import write_baseline

    base_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "base",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    baseline_path = tmp_path / "baseline.json"
    write_baseline(
        base_report,
        baseline_path,
        audit_log_path=tmp_path / "baseline-audit.log",
    )

    head_report, _ = run_scan(
        config_path=SAMPLE,
        output_dir=tmp_path / "head",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
        diff_from_path=tmp_path / "base" / "report.json",
        verification_context=VerificationContext(
            changed_files=[".agents-shipgate/baseline.json"]
        ),
        packet_enabled=False,
    )

    baseline_findings = [
        f
        for f in head_report.findings
        if f.check_id == "SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED"
        and f.evidence.get("kind") == "baseline_expanded"
    ]
    assert baseline_findings
    assert set(baseline_findings[0].evidence["added_fingerprints"]) == {
        f.fingerprint for f in head_report.findings if f.baseline_status == "matched"
    }


# --- SHIP-VERIFY-CI-GATE-REMOVED -------------------------------------------


def test_ci_gate_removed_when_workflow_deleted(tmp_path):
    # config_path's parent is the workspace; the workflow file does NOT
    # exist there, so a changed-files entry for it reads as a deletion.
    cfg = tmp_path / "shipgate.yaml"
    ctx = _context(
        changed_files=[".github/workflows/agents-shipgate.yml"],
        config_path=str(cfg),
    )
    findings = verify_ci_gate.run(ctx)
    assert len(findings) == 1
    assert findings[0].check_id == "SHIP-VERIFY-CI-GATE-REMOVED"
    assert findings[0].severity == "critical"
    assert findings[0].evidence["kind"] == "ci_gate_removed"


def test_ci_gate_present_emits_nothing(tmp_path):
    workflow = tmp_path / ".github" / "workflows" / "agents-shipgate.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: shipgate\n", encoding="utf-8")
    ctx = _context(
        changed_files=[".github/workflows/agents-shipgate.yml"],
        config_path=str(tmp_path / "shipgate.yaml"),
    )
    # File exists (modified, not removed) -> no finding.
    assert verify_ci_gate.run(ctx) == []


def test_ci_gate_no_verification_emits_nothing():
    assert verify_ci_gate.run(_context(verification=False)) == []


# --- SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED -------------------------------


def test_agent_instructions_weakened_on_change():
    findings = verify_agent_instructions.run(_context(changed_files=["AGENTS.md"]))
    assert len(findings) == 1
    assert findings[0].check_id == "SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED"
    assert findings[0].severity == "medium"


def test_agent_instructions_unrelated_file_emits_nothing():
    assert verify_agent_instructions.run(_context(changed_files=["src/app.py"])) == []


def test_agent_instructions_no_verification_emits_nothing():
    assert verify_agent_instructions.run(_context(verification=False)) == []


# --- SHIP-VERIFY-TRIGGER-CATALOG-DRIFT -------------------------------------


def test_trigger_catalog_drift_on_triggers_json():
    findings = verify_trigger_drift.run(
        _context(changed_files=["docs/triggers.json"])
    )
    assert len(findings) == 1
    assert findings[0].check_id == "SHIP-VERIFY-TRIGGER-CATALOG-DRIFT"


def test_trigger_catalog_unrelated_emits_nothing():
    assert verify_trigger_drift.run(_context(changed_files=["docs/README.md"])) == []


def test_trigger_catalog_no_verification_emits_nothing():
    assert verify_trigger_drift.run(_context(verification=False)) == []


# --- Tier A / Tier B case parity -------------------------------------------
#
# `trust_root_class_for` (Tier A, SHIP-VERIFY-TRUST-ROOT-TOUCHED) matches a
# path case-insensitively, because Git can carry a spelling that resolves to
# the canonical governance file on a case-insensitive filesystem. The
# specialized Tier B checks below all select their changed files through
# `_verify_common.touched()`. When that matched case-sensitively, a path was
# a trust root to Tier A and invisible to the specialized check that carries
# the real severity — a policy edit with no fail-safe, and a deleted CI gate
# with no critical finding.


@pytest.mark.parametrize(
    "path",
    ["services/foo/Policies/refund.yaml", "SHIPGATE.yaml", "Shipgate.yaml"],
)
def test_policy_weakened_fail_safe_sees_case_variant_trust_roots(path):
    assert trust_root_class_for(path) is not None, (
        f"Fixture drift: {path!r} is no longer a Tier A trust root."
    )
    findings = verify_policy.run(_context(base_policy=None, changed_files=[path]))
    assert len(findings) == 1, (
        f"{path!r} is a Tier A trust root but produced no policy fail-safe "
        f"finding; got {findings!r}."
    )
    assert findings[0].evidence["kind"] == "base_snapshot_unavailable"


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/Agents-Shipgate.yaml",
        ".github/workflows/AGENTS-SHIPGATE.yml",
        "services/foo/.github/workflows/Agents-Shipgate.yml",
    ],
)
def test_ci_gate_removed_sees_case_variant_workflow_paths(tmp_path, path):
    assert trust_root_class_for(path) == "ci_gate", (
        f"Fixture drift: {path!r} is no longer classified as a CI gate."
    )
    ctx = _context(changed_files=[path], config_path=str(tmp_path / "shipgate.yaml"))
    findings = verify_ci_gate.run(ctx)
    assert len(findings) == 1, (
        f"Deleting {path!r} is a CI-gate removal to Tier A but produced no "
        f"critical gate-removal finding; got {findings!r}."
    )
    assert findings[0].severity == "critical"
    assert findings[0].evidence["kind"] == "ci_gate_removed"


@pytest.mark.parametrize("path", ["docs/Triggers.json", "docs/TRIGGERS.JSON"])
def test_trigger_catalog_drift_sees_case_variant_catalog_paths(path):
    findings = verify_trigger_drift.run(_context(changed_files=[path]))
    assert len(findings) == 1, (
        f"{path!r} is the trigger catalog on a case-insensitive filesystem; "
        f"editing it must still route to review. Got {findings!r}."
    )
    assert findings[0].check_id == "SHIP-VERIFY-TRIGGER-CATALOG-DRIFT"


def test_agent_instructions_weakened_sees_case_variant_paths():
    findings = verify_agent_instructions.run(_context(changed_files=["agents.md"]))
    assert len(findings) == 1, (
        "`agents.md` resolves to AGENTS.md on a case-insensitive filesystem "
        f"and is a Tier A trust root; got {findings!r}."
    )
    assert findings[0].check_id == "SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED"
