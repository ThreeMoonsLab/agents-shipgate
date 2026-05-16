"""Schema round-trip tests (M4 · trust hardening).

Every committed schema under ``docs/`` (``manifest-v0.1.json``,
``checks.json``, the current-minor ``report-schema.v0.*.json``, and the
current-minor ``packet-schema.v0.*.json``) MUST match what
``scripts/generate_schemas.py`` produces from the live Pydantic models.

These tests call the generator's builder functions directly — no
subprocess, no I/O — so a Pydantic edit that forgets to regenerate
fails the test locally with a clear diff before CI runs the same check.

If a test here fails, run::

    python scripts/generate_schemas.py
    git add docs/ && git commit

That is the same remediation surfaced by
``scripts/generate_schemas.py --check`` in CI.
"""

from __future__ import annotations

import difflib
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_schemas.py"


def _load_generator():
    """Import scripts/generate_schemas.py without adding scripts/ to sys.path.

    importlib.util keeps the module local to this test file. The
    generator's top-level ``sys.path.insert(0, str(SRC))`` then makes
    ``agents_shipgate`` importable from a source checkout, the same as
    when the script runs standalone.
    """
    spec = importlib.util.spec_from_file_location(
        "agents_shipgate_schema_generator", GENERATOR_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"could not load {GENERATOR_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _load_generator()


def _assert_match(target: Path, generated_content: str) -> None:
    assert target.exists(), (
        f"{target.relative_to(REPO_ROOT)} is missing. "
        "Run `python scripts/generate_schemas.py` to create it."
    )
    committed = target.read_text(encoding="utf-8")
    if committed == generated_content:
        return
    diff = "".join(
        difflib.unified_diff(
            committed.splitlines(keepends=True),
            generated_content.splitlines(keepends=True),
            fromfile=f"{target.relative_to(REPO_ROOT)} (committed)",
            tofile=f"{target.relative_to(REPO_ROOT)} (generated)",
            n=2,
        )
    )
    pytest.fail(
        f"{target.relative_to(REPO_ROOT)} drifted from the live Pydantic "
        f"model. Run `python scripts/generate_schemas.py` and commit "
        f"the result.\n\n{diff}"
    )


def test_manifest_schema_matches_committed_file(generator):
    target, content = generator.build_manifest_schema()
    _assert_match(target, content)


def test_report_schema_matches_committed_file(generator):
    target, content = generator.build_report_schema()
    _assert_match(target, content)


def test_packet_schema_matches_committed_file(generator):
    target, content = generator.build_packet_schema()
    _assert_match(target, content)


def test_checks_catalog_matches_committed_file(generator):
    target, content = generator.build_checks_catalog()
    _assert_match(target, content)


def test_check_mode_passes_on_current_repo(generator, capsys):
    """End-to-end: `generate_schemas.py --check` exits 0 when artifacts match.

    Catches regressions in the --check wiring itself (e.g., a future
    refactor that bypasses one of the four builders).
    """
    exit_code = generator.main(["--check"])
    assert exit_code == 0, (
        "generate_schemas.py --check exited non-zero — at least one "
        "schema file drifted. Run `python scripts/generate_schemas.py` "
        "and commit."
    )


def test_check_mode_reports_drift(generator, tmp_path, monkeypatch, capsys):
    """Negative control: a synthetic drift must trigger exit 1 with a diff.

    Asserts the failure path is wired correctly so check-mode never
    silently passes on a real drift. Redirects DOCS to a temp tree so
    one stale file plus three missing files exercise both drift shapes
    (mismatch + missing).
    """
    monkeypatch.setattr(generator, "DOCS", tmp_path)
    stale_target = tmp_path / "manifest-v0.1.json"
    stale_target.write_text('{"stale": true}\n', encoding="utf-8")

    exit_code = generator.main(["--check"])
    err = capsys.readouterr().err

    assert exit_code == 1
    assert "drift detected" in err, "expected unified-diff preview for stale file"
    assert "missing" in err, "expected 'missing' marker for absent files"
    assert "python scripts/generate_schemas.py" in err, (
        "expected remediation command in failure output"
    )


def test_builders_are_pure(generator):
    """Build functions return ``(Path, str)`` and produce identical output
    on repeated calls.

    Locks the M4 invariant that builders are I/O-free and deterministic
    — the same model state must always produce byte-identical schema
    text, which is what makes the round-trip test trustworthy.
    """
    for builder in (
        generator.build_manifest_schema,
        generator.build_report_schema,
        generator.build_packet_schema,
        generator.build_checks_catalog,
    ):
        target_a, content_a = builder()
        target_b, content_b = builder()
        assert isinstance(target_a, Path)
        assert isinstance(content_a, str)
        assert target_a == target_b
        assert content_a == content_b, (
            f"{builder.__name__} produced different output on repeated call; "
            "generator is not deterministic."
        )
        assert content_a.endswith("\n"), (
            f"{builder.__name__} output missing trailing newline; canonical "
            "form requires it for stable git diffs."
        )
