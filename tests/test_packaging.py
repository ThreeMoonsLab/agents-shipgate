from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from email import message_from_bytes
from pathlib import Path

import pytest
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel exactly once per test session and return its path.

    Multiple packaging assertions need to introspect the wheel — the
    adoption-kit shape, the check-metadata YAML payload, and any
    future force-include addition. The wheel build is the slow step
    (~5s on a warm cache), so we amortise it across every test in
    this module via a session fixture.
    """
    pytest.importorskip("build", reason="python-build not installed")
    out_dir = tmp_path_factory.mktemp("dist")
    source_root = tmp_path_factory.mktemp("wheel-source") / "repo"
    shutil.copytree(
        REPO_ROOT,
        source_root,
        ignore=shutil.ignore_patterns(
            ".git",
            ".coverage",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "agents-shipgate-reports",
            "build",
            "dist",
            "htmlcov",
        ),
    )
    stale_report = (
        source_root / "samples" / "support_refund_agent" / "agents-shipgate-reports" / "report.json"
    )
    stale_report.parent.mkdir(parents=True)
    stale_report.write_text('{"stale": true}\n', encoding="utf-8")
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=source_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels!r}"
    return wheels[0]


@pytest.fixture(scope="session")
def installed_wheel_site(
    built_wheel: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Install the built wheel into an isolated target without network access."""

    root = tmp_path_factory.mktemp("installed-wheel")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--force-reinstall",
            "--target",
            str(root),
            str(built_wheel),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return root


def test_wheel_includes_adoption_kits(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        names = set(archive.namelist())
    assert "agents_shipgate/_adoption_kits/codex-skill/SKILL.md" in names
    assert "agents_shipgate/_adoption_kits/claude-code-skill/SKILL.md" in names
    assert "agents_shipgate/_adoption_kits/codex-skill/.agents-shipgate-kit-metadata.json" in names
    assert "agents_shipgate/_meta/claude-command/shipgate.md" in names


def test_wheel_includes_nested_sample_fixtures(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        names = set(archive.namelist())
    for path in (
        "agents_shipgate/_fixtures/agent_weakens_gate/.github/workflows/agents-shipgate.yml",
        "agents_shipgate/_fixtures/google_adk_agent/evals/support.eval.json",
        "agents_shipgate/_fixtures/support_refund_agent/.agents-shipgate/mcp-tools.json",
        "agents_shipgate/_fixtures/support_refund_agent/shipgate.yaml",
    ):
        assert path in names


def test_installed_wheel_replays_incident_fixtures(
    installed_wheel_site: Path,
    tmp_path: Path,
) -> None:
    """The three demos run from an installed artifact, not the source tree."""

    env = os.environ.copy()
    env["PYTHONPATH"] = str(installed_wheel_site)
    env["PYTHONNOUSERSITE"] = "1"
    fixtures = (
        (
            "agent_weakens_gate",
            "Merge verdict: blocked",
            "blocked",
            "blocked",
            "SHIP-VERIFY-CI-GATE-REMOVED",
            True,
        ),
        (
            "governed_edits_governance",
            "Fixture expectation: expected-fail",
            "mergeable",
            "passed",
            "SHIP-VERIFY-TRUST-ROOT-TOUCHED",
            False,
        ),
        (
            "capability_change_rides_release",
            "Merge verdict: human_review_required",
            "human_review_required",
            "review_required",
            "SHIP-VERIFY-TRUST-ROOT-TOUCHED",
            True,
        ),
    )

    def replay(
        expectation: tuple[str, str, str, str, str, bool],
    ) -> tuple[tuple[str, str, str, str, str, bool], Path, subprocess.CompletedProcess[str]]:
        name = expectation[0]
        out = tmp_path / name
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agents_shipgate",
                "fixture",
                "run",
                name,
                "--out",
                str(out),
            ],
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        return expectation, out, result

    with ThreadPoolExecutor(max_workers=len(fixtures)) as executor:
        results = list(executor.map(replay, fixtures))

    for expectation, out, result in results:
        name, expected_output, merge_verdict, decision, check_id, check_present = expectation
        assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"
        assert expected_output in result.stdout
        verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        assert verifier["merge_verdict"] == merge_verdict
        assert report["release_decision"]["decision"] == decision
        check_ids = {finding["check_id"] for finding in report["findings"]}
        assert (check_id in check_ids) is check_present


def test_wheel_excludes_generated_shipgate_reports(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        generated_reports = [
            name for name in archive.namelist() if "/agents-shipgate-reports/" in name
        ]
    assert generated_reports == [], (
        "generated scan outputs must never be included in the package, even "
        "when stale reports exist in the source tree: "
        f"{generated_reports}"
    )


def test_wheel_declares_verification_identity_runtime_dependencies(
    built_wheel: Path,
) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        metadata_path = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = message_from_bytes(archive.read(metadata_path))
    requirements = {
        Requirement(value).name.lower().replace("_", "-")
        for value in metadata.get_all("Requires-Dist", [])
    }
    assert "packaging" in requirements


def test_wheel_includes_org_policy_templates(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        names = set(archive.namelist())
    for template in (
        "org-release.shipgate.yaml",
        "payments.shipgate.yaml",
        "production-ops.shipgate.yaml",
        "host-boundary-strict.shipgate.yaml",
    ):
        assert f"agents_shipgate/_meta/policies/templates/{template}" in names


def test_wheel_claude_command_is_byte_identical_to_repo_file(
    built_wheel: Path,
) -> None:
    source = (REPO_ROOT / ".claude/commands/shipgate.md").read_bytes()
    with zipfile.ZipFile(built_wheel) as archive:
        packaged = archive.read("agents_shipgate/_meta/claude-command/shipgate.md")
    assert packaged == source


def test_wheel_ships_check_metadata_yamls_byte_identical(
    built_wheel: Path,
) -> None:
    """Every ``docs/checks/*.yaml`` must ship as
    ``agents_shipgate/_meta/checks/<name>.yaml`` with byte-identical
    content.

    Catches two regression classes that the loader alone cannot
    detect at import time:

    - Someone removes or rewrites the ``"docs/checks" =
      "agents_shipgate/_meta/checks"`` force-include in
      ``pyproject.toml``. With the entry missing, the wheel would
      ship without a catalog and ``_metadata_loader`` would raise
      ``RuntimeError`` on first import — but only after the wheel
      reached a user. This test fails at build/CI time instead.
    - ``hatch`` (or any future build backend) silently drops a YAML,
      renormalises line endings in a way the loader rejects, or
      reorders directory contents. Byte-identical comparison
      catches all three.
    """
    src_dir = REPO_ROOT / "docs" / "checks"
    expected = {p.name: p.read_bytes() for p in sorted(src_dir.glob("*.yaml"))}
    assert expected, (
        "pre-condition: docs/checks/*.yaml must exist in source. If "
        "this asserts, the YAML-driven catalog has been removed and "
        "this test should be deleted alongside it."
    )

    packaged: dict[str, bytes] = {}
    with zipfile.ZipFile(built_wheel) as archive:
        for name in archive.namelist():
            if name.startswith("agents_shipgate/_meta/checks/") and name.endswith(".yaml"):
                packaged[Path(name).name] = archive.read(name)

    missing = sorted(set(expected) - set(packaged))
    extra = sorted(set(packaged) - set(expected))
    assert not missing and not extra, (
        f"docs/checks/*.yaml ↔ wheel mismatch. Missing in wheel: "
        f"{missing}; extra in wheel: {extra}. Check the "
        "`[tool.hatch.build.targets.wheel.force-include]` block in "
        "pyproject.toml."
    )

    for name in sorted(expected):
        assert packaged[name] == expected[name], (
            f"agents_shipgate/_meta/checks/{name} differs in the "
            "wheel vs the source tree (byte-level diff). The build "
            "backend may be rewriting YAMLs in transit."
        )


def test_wheel_check_metadata_yamls_load_via_loader(
    built_wheel: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``load_check_metadata()`` produces the same
    ``CHECK_METADATA`` id set when pointed at the YAML payload
    *extracted from the wheel* as it does when pointed at the
    source tree.

    The byte-identical test above is a strong signal, but it doesn't
    prove the loader can actually parse the packaged copy — a future
    addition could make the loader rely on a dev-only filesystem
    layout (e.g., a sibling JSON sidecar, or an ``__init__.py``
    expected to exist). This test catches that class of regression
    by running the real loader against the real wheel payload.
    """
    from agents_shipgate.checks import _metadata_loader
    from agents_shipgate.checks.registry import CHECK_METADATA

    extract_dir = tmp_path / "extracted_checks"
    extract_dir.mkdir()
    with zipfile.ZipFile(built_wheel) as archive:
        for name in archive.namelist():
            if name.startswith("agents_shipgate/_meta/checks/") and name.endswith(".yaml"):
                (extract_dir / Path(name).name).write_bytes(archive.read(name))

    monkeypatch.setattr(_metadata_loader, "_resolve_checks_dir", lambda: extract_dir)
    packaged_catalog = _metadata_loader.load_check_metadata()

    source_ids = {c.id for c in CHECK_METADATA}
    packaged_ids = {c.id for c in packaged_catalog}
    assert packaged_ids == source_ids, (
        f"loader output differs between source and wheel. "
        f"Missing from wheel: {sorted(source_ids - packaged_ids)}; "
        f"extra in wheel: {sorted(packaged_ids - source_ids)}."
    )
    assert len(packaged_catalog) == len(CHECK_METADATA)
