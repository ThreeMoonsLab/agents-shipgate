"""Docs link integrity tests for the v0.7 agent-facing docs surface.

Per the v0.7 plan §3 verification:
- The three agent-facing docs exist on disk.
- Each is linked from `docs/INDEX.md` so agents walking the index can
  find them.
- Internal links inside the agent-facing docs resolve to files that
  actually exist (catches the "future-doc link" hazard from PR 1
  review).
- The stale `report-schema.v0.5.json` reference no longer appears in
  `docs/INDEX.md` (cleanup landed in PR 1).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
INDEX_MD = DOCS_DIR / "INDEX.md"

AGENT_FACING_DOCS = (
    "agent-recipes.md",
    "autofix-policy.md",
    "minimal-real-configs.md",
)


def test_agent_facing_docs_exist():
    for name in AGENT_FACING_DOCS:
        path = DOCS_DIR / name
        assert path.is_file(), f"Expected {path} to exist (v0.7 docs surface)"


def test_agent_facing_docs_linked_from_index():
    index_text = INDEX_MD.read_text(encoding="utf-8")
    for name in AGENT_FACING_DOCS:
        # Match the markdown link form `[label](agent-recipes.md)` or
        # `[label](agent-recipes.md "...")`. Index links use bare
        # filenames since the index lives in the same dir.
        link_pattern = re.compile(rf"\]\({re.escape(name)}(?:\s|\))")
        assert link_pattern.search(index_text), (
            f"docs/INDEX.md must link to docs/{name}; agents walking "
            "the index would otherwise miss this v0.7 surface."
        )


def test_index_no_longer_references_v05_schema():
    """v0.5 schema cleanup landed in PR 1; this test pins it so a
    future doc edit doesn't accidentally re-introduce the stale link."""
    index_text = INDEX_MD.read_text(encoding="utf-8")
    assert "report-schema.v0.5.json" not in index_text, (
        "docs/INDEX.md should no longer reference the v0.5 report schema; "
        "v0.6 is the frozen reference, v0.7 is current."
    )


def test_index_lists_current_v08_schema():
    """The current schema version moved to v0.8; the index must point
    agents at v0.8 for fresh report.json validation. v0.7 stays linked
    as the frozen reference for older reports."""
    index_text = INDEX_MD.read_text(encoding="utf-8")
    assert "report-schema.v0.8.json" in index_text, (
        "docs/INDEX.md must list report-schema.v0.8.json as the current schema "
        "since emitted reports carry report_schema_version: \"0.8\"."
    )
    assert "report-schema.v0.7.json" in index_text, (
        "docs/INDEX.md must keep report-schema.v0.7.json linked as the "
        "frozen reference for pre-v0.8 reports."
    )


def test_agent_recipes_internal_links_resolve():
    """Internal markdown links in `agent-recipes.md` (the doc most
    likely to grow forward references) must point at files that
    actually exist. Regression for the v0.7 PR 1 review where
    `autofix-policy.md` was linked before it shipped."""
    text = (DOCS_DIR / "agent-recipes.md").read_text(encoding="utf-8")
    # Match markdown links of the form [label](relative-path) where
    # the path is local (no scheme, no anchor-only).
    link_re = re.compile(r"\[[^\]]+\]\(([^)#:][^)#]*)(?:#[^)]*)?\)")
    for href in link_re.findall(text):
        href = href.strip()
        if not href or href.startswith(("http://", "https://", "mailto:")):
            continue
        target = (DOCS_DIR / "agent-recipes.md").parent / href
        target = target.resolve()
        assert target.exists(), (
            f"docs/agent-recipes.md links to non-existent {href} "
            f"(resolved to {target}). Update the link or land the "
            "missing file in the same PR."
        )


def test_autofix_policy_internal_links_resolve():
    """Same hazard — autofix-policy.md will accumulate references over
    time. Pin them now."""
    text = (DOCS_DIR / "autofix-policy.md").read_text(encoding="utf-8")
    link_re = re.compile(r"\[[^\]]+\]\(([^)#:][^)#]*)(?:#[^)]*)?\)")
    for href in link_re.findall(text):
        href = href.strip()
        if not href or href.startswith(("http://", "https://", "mailto:")):
            continue
        target = (DOCS_DIR / "autofix-policy.md").parent / href
        target = target.resolve()
        assert target.exists(), (
            f"docs/autofix-policy.md links to non-existent {href} "
            f"(resolved to {target})."
        )


def test_minimal_real_configs_internal_links_resolve():
    text = (DOCS_DIR / "minimal-real-configs.md").read_text(encoding="utf-8")
    link_re = re.compile(r"\[[^\]]+\]\(([^)#:][^)#]*)(?:#[^)]*)?\)")
    for href in link_re.findall(text):
        href = href.strip()
        if not href or href.startswith(("http://", "https://", "mailto:")):
            continue
        target = (DOCS_DIR / "minimal-real-configs.md").parent / href
        target = target.resolve()
        assert target.exists(), (
            f"docs/minimal-real-configs.md links to non-existent {href} "
            f"(resolved to {target})."
        )
