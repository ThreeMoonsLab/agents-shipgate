"""Coverage for ``agents_shipgate.checks._metadata_loader``.

The loader's *happy path* (75 builtin checks → identical
``CheckMetadata`` objects) is already exercised transitively by
``test_remediation_metadata.py``, ``test_severity_override_floor.py``,
``test_schema_roundtrip.py``, and the byte-identical
``docs/checks.json`` contract. This file covers the *error paths*
that exist only after the YAML-driven refactor: malformed top-level
shapes, duplicate ids across files, mismatched per-row category, and
the explicit ``docs_url`` ban.

Each test writes a self-contained YAML tree to a tmp dir and points
``_resolve_checks_dir`` at it via monkeypatch. Keeping the fixtures
inline (rather than under ``tests/fixtures/``) makes the failure mode
obvious from the test name and body.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agents_shipgate.checks import _metadata_loader

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write(dir: Path, name: str, body: str) -> Path:
    path = dir / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def fake_checks_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the loader at a tmp YAML directory.

    Bypasses the wheel/dev fallback in ``_resolve_checks_dir`` so each
    test gets a clean, deterministic catalog. The real
    ``_resolve_checks_dir`` is covered by the byte-identical
    ``docs/checks.json`` round-trip in
    ``test_schema_roundtrip``.
    """
    monkeypatch.setattr(
        _metadata_loader, "_resolve_checks_dir", lambda: tmp_path
    )
    return tmp_path


def test_loads_valid_yaml_and_injects_category_and_docs_url(
    fake_checks_dir: Path,
) -> None:
    _write(
        fake_checks_dir,
        "inventory.yaml",
        """
checks:
  - id: SHIP-FAKE-INVENTORY-X
    default_severity: high
    description: Fake inventory check for loader test.
""",
    )
    catalog = _metadata_loader.load_check_metadata()
    assert len(catalog) == 1
    entry = catalog[0]
    assert entry.id == "SHIP-FAKE-INVENTORY-X"
    assert entry.category == "inventory"  # injected from filename
    assert entry.docs_url == (
        "https://github.com/ThreeMoonsLab/agents-shipgate/blob/main/"
        "docs/checks.md#ship-fake-inventory-x"
    )
    # Defaults flowed through CheckMetadata.
    assert entry.mvp_tier == "hygiene"
    assert entry.autofix_safe is False
    assert entry.requires_human_review is True
    assert entry.suggested_patch_kind == "manual"


def test_returns_entries_sorted_by_id(fake_checks_dir: Path) -> None:
    _write(
        fake_checks_dir,
        "zeta.yaml",
        """
checks:
  - id: SHIP-ZETA-BBB
    default_severity: low
    description: Z-B.
  - id: SHIP-ZETA-AAA
    default_severity: low
    description: Z-A.
""",
    )
    _write(
        fake_checks_dir,
        "alpha.yaml",
        """
checks:
  - id: SHIP-ALPHA-CCC
    default_severity: low
    description: A-C.
""",
    )
    ids = [c.id for c in _metadata_loader.load_check_metadata()]
    assert ids == ["SHIP-ALPHA-CCC", "SHIP-ZETA-AAA", "SHIP-ZETA-BBB"]


def test_empty_yaml_file_is_tolerated(fake_checks_dir: Path) -> None:
    _write(
        fake_checks_dir,
        "comments_only.yaml",
        "# header-only file with no entries\n",
    )
    _write(
        fake_checks_dir,
        "real.yaml",
        """
checks:
  - id: SHIP-REAL-AAA
    default_severity: low
    description: present.
""",
    )
    ids = [c.id for c in _metadata_loader.load_check_metadata()]
    assert ids == ["SHIP-REAL-AAA"]


def test_duplicate_id_across_files_raises(fake_checks_dir: Path) -> None:
    _write(
        fake_checks_dir,
        "a_cat.yaml",
        """
checks:
  - id: SHIP-DUPE-ME
    default_severity: low
    description: a.
""",
    )
    _write(
        fake_checks_dir,
        "b_cat.yaml",
        """
checks:
  - id: SHIP-DUPE-ME
    default_severity: low
    description: b.
""",
    )
    with pytest.raises(ValueError, match="Duplicate check id 'SHIP-DUPE-ME'"):
        _metadata_loader.load_check_metadata()


def test_per_row_category_mismatch_raises(fake_checks_dir: Path) -> None:
    _write(
        fake_checks_dir,
        "inventory.yaml",
        """
checks:
  - id: SHIP-MISMATCH
    category: documentation
    default_severity: low
    description: wrong category.
""",
    )
    with pytest.raises(
        ValueError, match="declares category='documentation'.*filename implies"
    ):
        _metadata_loader.load_check_metadata()


def test_per_row_docs_url_is_rejected(fake_checks_dir: Path) -> None:
    _write(
        fake_checks_dir,
        "inventory.yaml",
        """
checks:
  - id: SHIP-EXPLICIT-DOCS-URL
    default_severity: low
    description: tries to override docs_url.
    docs_url: https://example.com/custom
""",
    )
    with pytest.raises(ValueError, match="declares an explicit docs_url"):
        _metadata_loader.load_check_metadata()


def test_missing_top_level_checks_key_raises(fake_checks_dir: Path) -> None:
    _write(fake_checks_dir, "broken.yaml", "not_checks: 42\n")
    with pytest.raises(
        ValueError, match="top-level YAML must be a mapping with a 'checks' key"
    ):
        _metadata_loader.load_check_metadata()


def test_checks_not_a_list_raises(fake_checks_dir: Path) -> None:
    _write(fake_checks_dir, "broken.yaml", "checks: not_a_list\n")
    with pytest.raises(ValueError, match="'checks' must be a list"):
        _metadata_loader.load_check_metadata()


def test_entry_without_id_raises(fake_checks_dir: Path) -> None:
    _write(
        fake_checks_dir,
        "inventory.yaml",
        """
checks:
  - default_severity: low
    description: no id present.
""",
    )
    with pytest.raises(ValueError, match="every entry needs a non-empty 'id'"):
        _metadata_loader.load_check_metadata()


def test_pydantic_validation_error_includes_check_id(
    fake_checks_dir: Path,
) -> None:
    _write(
        fake_checks_dir,
        "inventory.yaml",
        """
checks:
  - id: SHIP-BAD-SEVERITY
    default_severity: chartreuse
    description: invalid severity literal.
""",
    )
    with pytest.raises(
        ValueError,
        match="failed to construct CheckMetadata for 'SHIP-BAD-SEVERITY'",
    ):
        _metadata_loader.load_check_metadata()


def test_empty_directory_raises_runtime_error(
    fake_checks_dir: Path,
) -> None:
    # No YAML files written — directory exists but is empty.
    with pytest.raises(RuntimeError, match="No check metadata YAMLs found"):
        _metadata_loader.load_check_metadata()


def test_floor_severity_and_dynamic_default_round_trip(
    fake_checks_dir: Path,
) -> None:
    """Pin that the loader carries non-default optional fields through.

    These two fields are the ones most likely to be silently dropped by
    a refactor of the loader's row->kwargs translation. The remediation
    overrides (requires_human_review_regardless_of_patch,
    suggested_patch_kind) were exercised by the
    byte-identical docs/checks.json comparison in CI; this test gives
    them a direct, surface-level assertion too.
    """
    _write(
        fake_checks_dir,
        "action_surface.yaml",
        """
checks:
  - id: SHIP-FAKE-POLICY-VIOLATION
    default_severity: high
    mvp_tier: lifecycle
    floor_severity: medium
    dynamic_default: true
    description: fake dynamic-default check.
  - id: SHIP-FAKE-REMEDIATION
    default_severity: critical
    mvp_tier: core
    floor_severity: high
    description: fake high-risk check.
    requires_human_review_regardless_of_patch: true
    suggested_patch_kind: remove_pointer
""",
    )
    catalog = {c.id: c for c in _metadata_loader.load_check_metadata()}
    swing = catalog["SHIP-FAKE-POLICY-VIOLATION"]
    assert swing.mvp_tier == "lifecycle"
    assert swing.floor_severity == "medium"
    assert swing.dynamic_default is True
    high_risk = catalog["SHIP-FAKE-REMEDIATION"]
    assert high_risk.mvp_tier == "core"
    assert high_risk.requires_human_review_regardless_of_patch is True
    assert high_risk.suggested_patch_kind == "remove_pointer"


def test_builtin_check_yaml_entries_declare_mvp_tier() -> None:
    """Built-ins must make the MVP tier explicit.

    Third-party plugins may rely on the CheckMetadata default for
    compatibility, but the built-in catalog is a product surface and
    should not inherit this field accidentally.
    """

    missing: list[str] = []
    for path in sorted((REPO_ROOT / "docs" / "checks").glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for row in payload.get("checks", []):
            if "mvp_tier" not in row:
                missing.append(f"{path.name}:{row.get('id', '<missing id>')}")
    assert not missing, "Built-in checks missing mvp_tier: " + ", ".join(missing)
