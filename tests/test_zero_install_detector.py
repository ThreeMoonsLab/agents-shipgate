"""Golden-parity tests for the zero-install ``tools/shipgate-detect.py``.

Pins the script's structural verdict to ``agents-shipgate detect --json``
(via :func:`agents_shipgate.cli.discovery.detect_workspace`) on every
sample fixture in ``samples/``. The contract is **structural parity**,
not byte parity: same ``is_agent_project``, same set of fired
frameworks, same ``suggested_sources``. Evidence strings and absolute
scores are intentionally simplified — a coding agent uses the script
to make a yes/no decision, not to re-derive the report.

If a new sample is added or the canonical detection rules change, this
test catches drift between the script and the CLI immediately.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from agents_shipgate.cli.discovery import detect_workspace

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "tools" / "shipgate-detect.py"
SAMPLES_ROOT = REPO_ROOT / "samples"

# Hidden directories under samples/ are reference material (anti-patterns,
# READMEs), not detector inputs. The published fixtures are the regular
# top-level dirs.
_HIDDEN_PREFIXES = ("_", ".")

CANONICAL_KEYS = frozenset(
    {
        "is_agent_project",
        "frameworks",
        "agent_name_candidates",
        "project_name_candidates",
        "suggested_sources",
        "next_action",
        "workspace_signals",
    }
)


def _sample_dirs() -> list[Path]:
    return sorted(
        p
        for p in SAMPLES_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(_HIDDEN_PREFIXES)
    )


def _sample_ids() -> list[str]:
    return [p.name for p in _sample_dirs()]


def _load_script_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "shipgate_detect_zero_install", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["shipgate_detect_zero_install"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


def test_script_path_exists():
    """The zero-install script must live at the published path. The
    raw GitHub URL in docs/zero-install.md and llms.txt is what coding
    agents fetch — moving the file silently would break the public
    contract."""
    assert SCRIPT_PATH.is_file(), (
        f"tools/shipgate-detect.py not found at {SCRIPT_PATH}. The "
        "raw GitHub URL is part of the agent-facing public surface."
    )


def test_script_does_not_claim_drop_in_parity(script_module):
    """The script is documented as a structural subset of
    ``agents-shipgate detect --json``, NOT a drop-in replacement.
    Specifically, the canonical CLI emits ``diagnostics[]`` and
    ``next_actions[]`` arrays; the zero-install script does not.

    Pin the absence so docs/zero-install.md, llms.txt, and the script's
    docstring stay accurate. If we ever decide to ship a stdlib-only
    diagnostic engine, update those wording surfaces in the same PR
    that flips this test."""
    result = script_module.detect(SAMPLES_ROOT / "support_refund_agent")
    assert "diagnostics" not in result, (
        "The zero-install script must not emit `diagnostics[]` — it's "
        "documented as a structural subset of the canonical CLI. If "
        "you add this field, update the docstring in "
        "tools/shipgate-detect.py, docs/zero-install.md, and llms.txt "
        "to match."
    )
    assert "next_actions" not in result, (
        "The zero-install script must not emit `next_actions[]` — see "
        "the docstring in tools/shipgate-detect.py for the rationale "
        "(diagnostic engine is out of scope for the zero-install path)."
    )


def test_script_emits_canonical_top_level_keys(script_module):
    """The script's JSON output must carry the same top-level keys as
    DetectResult, plus ``script_version`` to distinguish it from the
    canonical CLI."""
    result = script_module.detect(SAMPLES_ROOT / "support_refund_agent")
    missing = CANONICAL_KEYS - set(result)
    assert not missing, (
        f"Zero-install detector output missing canonical keys: {sorted(missing)}. "
        "Output must be a structural superset of DetectResult."
    )
    assert "script_version" in result, (
        "Zero-install detector must emit script_version so consumers "
        "can distinguish it from the canonical CLI's output."
    )


@pytest.mark.parametrize("sample_dir", _sample_dirs(), ids=_sample_ids())
def test_script_verdict_matches_cli(script_module, sample_dir):
    """Structural parity: for every sample, the zero-install script
    must agree with the canonical CLI on (a) ``is_agent_project``,
    (b) the set of fired frameworks, (c) the set of suggested-source
    types and paths, and (d) workspace-signals keys."""
    script_result = script_module.detect(sample_dir)
    cli_result = detect_workspace(sample_dir.resolve()).model_dump(mode="json")

    assert script_result["is_agent_project"] == cli_result["is_agent_project"], (
        f"{sample_dir.name}: is_agent_project diverged "
        f"(script={script_result['is_agent_project']}, "
        f"cli={cli_result['is_agent_project']})."
    )

    script_frameworks = sorted(f["type"] for f in script_result["frameworks"])
    cli_frameworks = sorted(f["type"] for f in cli_result["frameworks"])
    assert script_frameworks == cli_frameworks, (
        f"{sample_dir.name}: framework set diverged "
        f"(script={script_frameworks!r}, cli={cli_frameworks!r}). "
        "The script's scoring rules must match cli/discovery/signals.py."
    )

    script_sources = sorted(
        (s["type"], s["path"]) for s in script_result["suggested_sources"]
    )
    cli_sources = sorted(
        (s["type"], s["path"]) for s in cli_result["suggested_sources"]
    )
    assert script_sources == cli_sources, (
        f"{sample_dir.name}: suggested_sources diverged "
        f"(script={script_sources!r}, cli={cli_sources!r})."
    )

    cli_signals = cli_result["workspace_signals"]
    script_signals = script_result["workspace_signals"]
    assert set(script_signals) == set(cli_signals), (
        f"{sample_dir.name}: workspace_signals keys diverged "
        f"(script={set(script_signals)!r}, cli={set(cli_signals)!r})."
    )


@pytest.mark.parametrize("sample_dir", _sample_dirs(), ids=_sample_ids())
def test_script_finds_at_least_one_python_file_when_cli_does(
    script_module, sample_dir
):
    """The script's ``os.walk`` and the CLI's git-aware walker may
    legitimately differ on file counts (e.g. samples with build
    artifacts), but if the CLI sees Python files in a sample, the
    script must too — otherwise framework detection is impossible."""
    script_result = script_module.detect(sample_dir)
    cli_result = detect_workspace(sample_dir.resolve()).model_dump(mode="json")
    cli_count = cli_result["workspace_signals"]["python_file_count"]
    script_count = script_result["workspace_signals"]["python_file_count"]
    if cli_count > 0:
        assert script_count > 0, (
            f"{sample_dir.name}: CLI found {cli_count} python files but "
            f"the zero-install script found 0. Walk logic diverged — "
            "check SKIP_DIRS and the os.walk pruning."
        )
