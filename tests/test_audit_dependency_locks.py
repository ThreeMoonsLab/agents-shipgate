from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.version import Version

from scripts import audit_dependency_locks

HASH = "0" * 64


def _write_lock(root: Path, filename: str, *pins: str) -> None:
    target = root / "constraints" / filename
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        "".join(f"{pin} \\\n    --hash=sha256:{HASH}\n" for pin in pins), encoding="utf-8"
    )


def test_real_pip_audit_queries_the_locked_build_version(tmp_path: Path, monkeypatch) -> None:
    from pip_audit import _cli
    from pip_audit._dependency_source import requirement
    from pip_audit._service import PyPIService
    from pip_audit._service.interface import VulnerabilityResult

    _write_lock(tmp_path, "dev.txt", "test-only==4.0", "shared==1.0")
    _write_lock(
        tmp_path, "build-backend.txt", "shared==1.0", "build-only==1.0; python_version >= '3.12'",
        "build-only==2.0; python_version < '3.12'",
    )
    queried = set()

    def query(self, dependency):
        pin = (dependency.canonical_name, str(dependency.version))
        queried.add(pin)
        findings = []
        if pin == ("build-only", "1.0"):
            findings.append(VulnerabilityResult("TEST-ADVISORY", "Fixture only", [Version("2.0")], set()))
        return dependency, findings

    def unexpected_resolution(*args, **kwargs):
        pytest.fail("the audit attempted to resolve packages")

    def run(argv, *, check):
        assert argv[:3] == [sys.executable, "-m", "pip_audit"]
        assert "--no-deps" in argv and "--disable-pip" in argv
        with monkeypatch.context() as context:
            context.setattr(sys, "argv", argv[2:])
            with pytest.raises(SystemExit) as result:
                _cli.audit()
        return SimpleNamespace(returncode=result.value.code)

    monkeypatch.setattr(PyPIService, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(PyPIService, "query", query)
    monkeypatch.setattr(requirement, "VirtualEnv", unexpected_resolution)
    monkeypatch.setattr(audit_dependency_locks, "subprocess", SimpleNamespace(run=run))

    out = tmp_path / "evidence"
    assert audit_dependency_locks.audit(tmp_path, out, "test-build") == 1
    assert queried == {("build-only", "1.0"), ("shared", "1.0"), ("test-only", "4.0")}
    evidence = json.loads((out / "evidence.json").read_bytes())
    assert evidence["status"] == "findings"
    assert evidence["finding_count"] == 1
    assert evidence["inventory_sha256"] == hashlib.sha256((out / "requirements.txt").read_bytes()).hexdigest()
    assert evidence["locks"] == [
        {"path": name, "sha256": hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}
        for name in ("constraints/dev.txt", "constraints/build-backend.txt")
    ]


@pytest.mark.parametrize("scenario", [
    "complete", "skipped", "missing", "different_version", "installed_mismatch", "unsupported",
])
def test_a_publishing_audit_requires_complete_exact_pin_coverage(tmp_path: Path, monkeypatch, scenario) -> None:
    pins = ["publish-only==1.0"]
    if scenario == "unsupported":
        pins.append("direct @ https://example.invalid/direct.whl")
    _write_lock(tmp_path, "release-publish.txt", *pins)
    publish = {"name": "publish-only", "version": "1.0", "vulns": []}
    dependencies = {
        "skipped": [{"name": "publish-only", "skip_reason": "not in index"}],
        "missing": [],
        "different_version": [{**publish, "version": "2.0"}],
    }

    def run(argv, *, check):
        assert Path(argv[argv.index("--requirement") + 1]).read_text() == "publish-only==1.0\n"
        report = Path(argv[argv.index("--output") + 1])
        report.write_text(json.dumps({"dependencies": dependencies.get(scenario, [publish])}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(audit_dependency_locks, "subprocess", SimpleNamespace(run=run))
    monkeypatch.setattr(audit_dependency_locks.metadata, "version", lambda name: "2.0")
    out = tmp_path / "evidence"
    assert audit_dependency_locks.audit(
        tmp_path, out, "release-publishing", check_installed=scenario == "installed_mismatch"
    ) == (0 if scenario == "complete" else 2)
    evidence = json.loads((out / "evidence.json").read_bytes())
    assert evidence["status"] == ("passed" if scenario == "complete" else "incomplete")
    if scenario == "skipped":
        assert evidence["skipped"] == [{"name": "publish-only", "reason": "not in index"}]
    elif scenario == "installed_mismatch":
        assert evidence["installed_mismatches"] == [{"name": "publish-only", "pinned": "1.0", "installed": "2.0"}]
    elif scenario == "unsupported":
        assert [(pin["name"], pin["reason"]) for pin in evidence["unsupported"]] == [("direct", "direct URL")]
    elif scenario != "complete":
        assert evidence["missing_or_mismatched"] == ["publish-only"]
