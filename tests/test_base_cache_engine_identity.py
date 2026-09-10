"""A cached base must have been scanned by the engine answering this run."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_packaging import built_wheel, installed_wheel_site  # noqa: F401
from test_verify_weakening import _weakened_repo

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE = r'''
import json
import sys
from pathlib import Path
from agents_shipgate import __version__
from agents_shipgate.cli.verify import orchestrator
from test_verify_weakening import _run_verify

calls = []
original = orchestrator.run_scan
def observed_scan(*args, **kwargs):
    config = kwargs["config_path"]
    if any(p.name == "base" and p.parent.name.startswith("agents-shipgate-verify-")
           for p in config.parents):
        calls.append(str(config))
    return original(*args, **kwargs)
orchestrator.run_scan = observed_scan
repo = Path(sys.argv[1])
_, report, _ = _run_verify(repo)
assert report is not None
plan = json.loads((repo / "agents-shipgate-reports/verification-plan.json").read_text())
print("CACHE_PROBE " + json.dumps({
    "version": __version__,
    "engine": plan["engine"],
    "request_id": plan["request_id"],
    "base_scans": len(calls),
    "policy_kinds": sorted(f.evidence.get("kind", "") for f in report.findings
                           if f.check_id == "SHIP-VERIFY-POLICY-WEAKENED"),
}))
'''


def run_engine(python_path: Path, repo: Path) -> dict:
    env = os.environ.copy()
    env.update(
        PYTHONPATH=os.pathsep.join([str(python_path), str(REPO_ROOT / "tests")]),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(repo)],
        cwd=repo.parent, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(next(
        line.removeprefix("CACHE_PROBE ")
        for line in result.stdout.splitlines() if line.startswith("CACHE_PROBE ")
    ))


@pytest.mark.parametrize("layout", ["source_checkout", "installed_wheel"])
@pytest.mark.parametrize("change", ["reader_behavior", "legacy_version_key"])
def test_actual_same_version_engine_change_rescans_base(tmp_path, request, layout, change):
    engine = tmp_path / "engine"
    if layout == "installed_wheel":
        shutil.copytree(request.getfixturevalue("installed_wheel_site"), engine)
        python_path = engine
    else:
        shutil.copytree(REPO_ROOT, engine, ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
            ".coverage", "agents-shipgate-reports", "build", "dist", "htmlcov",
        ))
        python_path = engine / "src"
    if change == "reader_behavior":
        reader = python_path / "agents_shipgate/core/lenses/effective_policy.py"
        before = 'ci_mode=getattr(ci, "mode", None),'
        after = 'ci_mode="advisory",'
    else:
        reader = python_path / "agents_shipgate/cli/verify/orchestrator.py"
        before = '        "engine_requirement_id": engine.engine_requirement_id,\n'
        after = ''
    current_source = reader.read_text()
    assert before in current_source
    # Recreate the old producer's actual behavior in an isolated engine. This
    # is not a forged report or a version/epoch bump: both runs execute code.
    reader.write_text(current_source.replace(before, after, 1))
    repo = _weakened_repo(tmp_path, sample_dir=Path("samples/clean_read_only_agent"))
    old = run_engine(python_path, repo)
    assert old["base_scans"] == 1
    assert ("ci_mode_weakened" in old["policy_kinds"]) == (change == "legacy_version_key")
    repeated_old = run_engine(python_path, repo)
    assert repeated_old["base_scans"] == 0
    assert repeated_old["engine"] == old["engine"]
    assert repeated_old["request_id"] == old["request_id"]

    reader.write_text(current_source)
    current = run_engine(python_path, repo)
    assert current["version"] == old["version"]
    assert current["engine"]["engine_distribution_sha256"] != old["engine"][
        "engine_distribution_sha256"
    ]
    assert current["base_scans"] == 1
    assert "ci_mode_weakened" in current["policy_kinds"]
    repeated_current = run_engine(python_path, repo)
    assert repeated_current["base_scans"] == 0
    assert repeated_current["engine"] == current["engine"]
    assert repeated_current["request_id"] == current["request_id"]
    assert repeated_current["policy_kinds"] == current["policy_kinds"]


def _base_scan_observer(monkeypatch):
    from agents_shipgate.cli.verify import orchestrator

    calls = []
    original = orchestrator.run_scan

    def observe(*args, **kwargs):
        config = kwargs['config_path']
        if any(p.name == 'base' and p.parent.name.startswith('agents-shipgate-verify-')
               for p in config.parents):
            calls.append(config)
        return original(*args, **kwargs)

    monkeypatch.setattr(orchestrator, 'run_scan', observe)
    return calls


def _cache_entry(repo):
    (entry,) = (repo / '.git/agents-shipgate/base-scans').glob('*/report.json')
    return entry


@pytest.mark.parametrize('damage', [
    'missing_checksum', 'non_ascii_checksum', 'oversized_checksum', 'wrong_checksum',
    'invalid_json', 'invalid_model', 'missing_schema', 'old_schema',
])
def test_corrupt_cache_is_regenerated_from_git(tmp_path, monkeypatch, damage):
    import hashlib

    from test_verify_weakening import _policy_kinds, _run_verify

    from agents_shipgate.cli.verify import orchestrator

    repo = _weakened_repo(tmp_path, sample_dir=Path('samples/clean_read_only_agent'))
    scans = _base_scan_observer(monkeypatch)
    _run_verify(repo)
    entry = _cache_entry(repo)
    assert orchestrator._cache_report_valid(entry)
    _run_verify(repo)
    assert len(scans) == 1  # Positive control: this engine reuses a valid entry.
    checksum = entry.with_suffix('.sha256')
    if damage == 'missing_checksum':
        checksum.unlink()
    elif damage == 'non_ascii_checksum':
        checksum.write_bytes(b'\xff')
    elif damage == 'oversized_checksum':
        checksum.write_bytes(b'0' * 1024)
    elif damage == 'wrong_checksum':
        checksum.write_text('0' * 64)
    else:
        payload = json.loads(entry.read_text())
        if damage == 'invalid_json':
            entry.write_text('{')
        elif damage == 'invalid_model':
            entry.write_text('{}')
        else:
            if damage == 'missing_schema':
                payload.pop('report_schema_version')
            else:
                payload['report_schema_version'] = '0.1'
            entry.write_text(json.dumps(payload))
        # A matching checksum is insufficient for malformed/incompatible data.
        checksum.write_text(hashlib.sha256(entry.read_bytes()).hexdigest())
    assert not orchestrator._cache_report_valid(entry)
    verifier, report, _ = _run_verify(repo)
    assert len(scans) == 2
    assert 'ci_mode_weakened' in _policy_kinds(report)
    assert 'Regenerating cached base report.json from Git' in verifier.model_dump_json()
    assert orchestrator._cache_report_valid(entry)
    _run_verify(repo)
    assert len(scans) == 2


def test_directory_at_cache_report_is_preserved_and_routes_to_repair(tmp_path, monkeypatch):
    from test_verify_weakening import _run_verify

    repo = _weakened_repo(tmp_path, sample_dir=Path('samples/clean_read_only_agent'))
    scans = _base_scan_observer(monkeypatch)
    _run_verify(repo)
    entry = _cache_entry(repo)
    entry.unlink()
    entry.mkdir()
    marker = entry / 'user-data'
    marker.write_text('preserve this')
    verifier, report, _ = _run_verify(repo)
    assert len(scans) == 2
    assert verifier.base_status == 'scan_failed'
    assert report.release_decision.decision == 'review_required'
    assert not verifier.control.permissions.merge
    assert not verifier.control.permissions.report_complete
    assert 'Could not store the regenerated base report' in verifier.model_dump_json()
    assert 'then rerun verification' in verifier.model_dump_json()
    assert marker.read_text() == 'preserve this'


def test_engine_identity_is_shared_with_plan_but_rebuilt_next_run(tmp_path, monkeypatch):
    from test_verify_weakening import _run_verify

    from agents_shipgate.cli.verify import orchestrator
    from agents_shipgate.core import verification_identity

    repo = _weakened_repo(tmp_path, sample_dir=Path('samples/clean_read_only_agent'))
    original = verification_identity.build_engine_requirement
    captures = []

    def observe(**kwargs):
        engine = original(**kwargs)
        captures.append(engine)
        return engine

    monkeypatch.setattr(orchestrator, 'build_engine_requirement', observe)
    monkeypatch.setattr(verification_identity, 'build_engine_requirement', observe)
    _run_verify(repo)
    assert len(captures) == 1
    plan = json.loads((repo / 'agents-shipgate-reports/verification-plan.json').read_text())
    assert plan['engine'] == captures[-1].model_dump(mode='json')
    _run_verify(repo)
    assert len(captures) == 2
    assert captures[0] == captures[1]
    assert captures[0] is not captures[1]
    verification_identity.validate_engine_requirement(captures[0], plugins_enabled=False)
    assert len(captures) == 3  # Validation independently observes the current engine.


def test_missing_engine_identity_refuses_even_a_valid_warm_cache(tmp_path, monkeypatch):
    from test_verify_weakening import _run_verify

    from agents_shipgate.cli.verify import orchestrator

    repo = _weakened_repo(tmp_path, sample_dir=Path('samples/clean_read_only_agent'))
    scans = _base_scan_observer(monkeypatch)
    _run_verify(repo)
    entry = _cache_entry(repo)
    original_bytes = entry.read_bytes()
    assert orchestrator._cache_report_valid(entry)

    def unavailable():
        raise OSError('private install details must not leak')

    status, _, cached, lock, notes = orchestrator._prepare_base_report(
        git_root=repo, base='HEAD~1',
        config_relative=Path('samples/support_refund_agent/shipgate.yaml'),
        baseline_path=None, policy_packs=[], plugins_enabled=False,
        no_heuristics=False, verbose=False, evaluation_date='2026-09-10',
        engine_requirement_factory=unavailable,
    )
    assert status == 'scan_failed'
    assert cached is None and lock is None
    assert len(scans) == 1
    assert entry.read_bytes() == original_bytes
    assert 'doctor --json' in ' '.join(notes)
    assert 'private install details' not in ' '.join(notes)


@pytest.mark.parametrize("linked_file", ["report.json", "report.sha256"])
def test_cache_repair_replaces_file_link_without_changing_target(tmp_path, monkeypatch, linked_file):
    from test_verify_weakening import _policy_kinds, _run_verify

    from agents_shipgate.cli.verify import orchestrator

    repo = _weakened_repo(tmp_path, sample_dir=Path("samples/clean_read_only_agent"))
    scans = _base_scan_observer(monkeypatch)
    _run_verify(repo)
    entry = _cache_entry(repo)
    selected = entry.with_name(linked_file)
    target = tmp_path / "external-file"
    original_bytes = selected.read_bytes()
    target.write_bytes(original_bytes)
    selected.unlink()
    selected.symlink_to(target)
    assert not orchestrator._cache_report_valid(entry)
    _, report, _ = _run_verify(repo)
    assert len(scans) == 2
    assert "ci_mode_weakened" in _policy_kinds(report)
    assert not selected.is_symlink()
    assert target.read_bytes() == original_bytes
    assert orchestrator._cache_report_valid(entry)
