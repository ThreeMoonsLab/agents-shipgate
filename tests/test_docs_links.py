"""Docs link integrity tests for the v0.7 agent-facing docs surface.

Per the agent-facing docs verification:
- The agent-facing docs exist on disk.
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
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)#:][^)#]*)(?:#[^)]*)?\)")

# Derive the current report-schema version from the runtime model so
# the docs-link test bumps automatically when the schema bumps. The
# previous hardcoded literal ("v0.11") let the docs go stale relative
# to the runtime — see PR #57 review P2.
def _current_report_schema_version() -> str:
    from agents_shipgate.core.models import ReadinessReport

    return str(ReadinessReport.model_fields["report_schema_version"].default)


def _previous_report_schema_version() -> str:
    """The next-most-recent schema version, expected to remain linked
    from the index as a frozen reference. Derived as ``current - 1``
    on the minor."""
    current = _current_report_schema_version()
    major, minor = current.split(".")
    return f"{major}.{int(minor) - 1}"

AGENT_FACING_DOCS = (
    "agent-recipes.md",
    "agent-adoption-harness.md",
    "autofix-policy.md",
    "minimal-real-configs.md",
    "target-repo-agent-snippets.md",
)

ADOPTION_DOCS_WITH_LINKS = (
    DOCS_DIR / "target-repo-agent-snippets.md",
    DOCS_DIR / "agent-adoption-harness.md",
    REPO_ROOT / "examples" / "golden-prs" / "README.md",
    REPO_ROOT / "examples" / "golden-prs" / "openai-agents-sdk-refund-agent" / "README.md",
    REPO_ROOT / "examples" / "golden-prs" / "mcp-only-tool-server" / "README.md",
    REPO_ROOT / "examples" / "golden-prs" / "openapi-support-agent" / "README.md",
)


def _assert_internal_links_resolve(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for href in LINK_RE.findall(text):
        href = href.strip()
        if not href or href.startswith(("http://", "https://", "mailto:")):
            continue
        target = (path.parent / href).resolve()
        assert target.exists(), f"{path} links to non-existent {href} (resolved to {target})."


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


def test_gitignore_keeps_private_agent_notes_out_of_commits():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".agents-private/" in text


def test_index_no_longer_references_v05_schema():
    """v0.5 schema cleanup landed in PR 1; this test pins it so a
    future doc edit doesn't accidentally re-introduce the stale link."""
    index_text = INDEX_MD.read_text(encoding="utf-8")
    assert "report-schema.v0.5.json" not in index_text, (
        "docs/INDEX.md should no longer reference the v0.5 report schema; "
        "v0.6 is the frozen reference, v0.7 is current."
    )


def test_index_lists_current_schema():
    """The index must point agents at the current schema for fresh
    report.json validation; the immediately-previous schema must stay
    linked as the frozen reference. The expected version is derived
    from the runtime model (``ReadinessReport.report_schema_version``),
    so a schema bump that forgets to update docs/INDEX.md will fail
    this test in the same PR — preventing the doc-drift trap from
    PR #57 review P2."""
    index_text = INDEX_MD.read_text(encoding="utf-8")
    current = _current_report_schema_version()
    previous = _previous_report_schema_version()

    assert f"report-schema.v{current}.json" in index_text, (
        f"docs/INDEX.md must list report-schema.v{current}.json as the "
        f"current schema (the runtime model's default is {current!r}). "
        "Update the docs in the same PR as the schema bump."
    )
    assert f"report-schema.v{previous}.json" in index_text, (
        f"docs/INDEX.md must keep report-schema.v{previous}.json linked "
        f"as the frozen reference for pre-v{current} reports."
    )


def test_no_doc_falsely_advertises_an_older_schema_as_current():
    """No file under ``docs/`` may advertise an older report schema as
    "current". The contract-test pattern at
    tests/test_public_surface_contract.py covers PUBLIC_SURFACES; this
    test extends the guard to **every** Markdown file under ``docs/``.

    Earlier hand-curated lists missed `docs/overview.md` and
    `docs/ai-search-summary.md` (#57 review P3), forcing two more
    drift fixes. Walking the full tree closes that loophole — adding
    a new doc that mentions the schema cannot bypass the guard."""
    current = _current_report_schema_version()
    older_minor = re.compile(r"report-schema\.v0\.(?P<minor>\d+)\.json")
    current_minor = int(current.split(".")[1])

    # Schema files themselves (`docs/report-schema.v0.X.json`) and
    # private adoption notes are excluded; everything else under
    # ``docs/`` is scanned.
    failures: list[str] = []
    for path in DOCS_DIR.rglob("*.md"):
        relpath = path.relative_to(DOCS_DIR).as_posix()
        text = path.read_text(encoding="utf-8")
        for match in older_minor.finditer(text):
            mentioned = int(match.group("minor"))
            if mentioned >= current_minor:
                continue
            # Find the surrounding context (~one paragraph) and confirm
            # this older mention is labeled as a frozen/legacy/older
            # reference, not as "current".
            start = max(0, match.start() - 200)
            end = min(len(text), match.end() + 200)
            context = text[start:end].lower()
            if "current" in context and not any(
                marker in context
                for marker in ("frozen", "legacy", "older", "pre-v")
            ):
                failures.append(
                    f"docs/{relpath}: report-schema.v0.{mentioned}.json "
                    "near the word 'current' without a "
                    "frozen/legacy/older marker"
                )

    assert not failures, (
        "Doc drift: the following docs mention an older report schema as "
        f"'current' (runtime is v{current}). Bump them or add a "
        "frozen/legacy marker:\n  - " + "\n  - ".join(failures)
    )


def test_trust_model_documents_bounded_git_discovery_exception():
    """The trust model must keep the local git subprocess exception explicit.

    Discovery can use git to avoid private/cache/generated workspace noise, but
    the no-user-code/no-network boundary still holds.
    """
    text = (DOCS_DIR / "trust-model.md").read_text(encoding="utf-8")
    assert "- shell out to subprocesses;" not in text
    assert "bounded local `git` discovery" in text
    assert "`rev-parse` and `ls-files`" in text
    assert "does not contact remotes" in text
    assert "run framework CLIs" in text


def test_target_repo_snippets_pin_advisory_agent_contract():
    text = (DOCS_DIR / "target-repo-agent-snippets.md").read_text(encoding="utf-8")
    assert "release_decision.decision" in text
    assert "agents-shipgate-reports/report.json" in text
    assert "ci_mode: advisory" in text
    assert 'pr_comment: "true"' in text
    assert "apply-patches" in text
    assert "--confidence high --apply" in text
    assert "Do not auto-assert approval" in text
    assert "confirmation" in text
    assert "idempotency" in text
    assert "broad-scope" in text
    assert "prohibited-action" in text
    assert "pure docs, tests, formatting" in text


def test_target_repo_cursor_globs_cover_shipgate_discovery_names():
    text = (DOCS_DIR / "target-repo-agent-snippets.md").read_text(encoding="utf-8")
    assert '"**/*openapi*.yaml"' in text
    assert '"**/*openapi*.yml"' in text
    assert '"**/*openapi*.json"' in text
    assert '"**/*swagger*.yaml"' in text
    assert '"**/*swagger*.yml"' in text
    assert '"**/*swagger*.json"' in text
    assert '"**/*mcp*.json"' in text


def test_agent_adoption_harness_is_manual_and_keeps_results_private():
    text = (DOCS_DIR / "agent-adoption-harness.md").read_text(encoding="utf-8")
    assert "Do not automate calls" in text
    assert ".agents-private/adoption-sprint/" in text
    assert "`.agents-private/` to `.gitignore`" in text
    assert "100-Point Rubric" in text
    assert "negative-control non-agent repo" in text
    assert "release_decision.decision" in text


def test_golden_pr_examples_exist_and_reference_real_samples():
    root = REPO_ROOT / "examples" / "golden-prs"
    expected = {
        "openai-agents-sdk-refund-agent": REPO_ROOT / "samples" / "support_refund_agent",
        "mcp-only-tool-server": REPO_ROOT
        / "samples"
        / "support_refund_agent"
        / ".agents-shipgate"
        / "mcp-tools.json",
        "openapi-support-agent": REPO_ROOT
        / "samples"
        / "support_refund_agent"
        / "specs"
        / "support-tools.openapi.yaml",
    }
    assert (root / "README.md").is_file()
    for dirname, sample_path in expected.items():
        readme = root / dirname / "README.md"
        assert readme.is_file(), f"Missing golden PR README: {readme}"
        assert sample_path.exists(), f"Golden PR sample reference is missing: {sample_path}"
        text = readme.read_text(encoding="utf-8").lower()
        assert "release decision" in text
        assert "human" in text
        assert "pr summary" in text
        assert "advisory pr comment shape" not in text


def test_new_adoption_docs_internal_links_resolve():
    for path in ADOPTION_DOCS_WITH_LINKS:
        assert path.is_file(), f"Expected adoption doc to exist: {path}"
        _assert_internal_links_resolve(path)


def test_agent_recipes_internal_links_resolve():
    """Internal markdown links in `agent-recipes.md` (the doc most
    likely to grow forward references) must point at files that
    actually exist. Regression for the v0.7 PR 1 review where
    `autofix-policy.md` was linked before it shipped."""
    _assert_internal_links_resolve(DOCS_DIR / "agent-recipes.md")


def test_autofix_policy_internal_links_resolve():
    """Same hazard — autofix-policy.md will accumulate references over
    time. Pin them now."""
    _assert_internal_links_resolve(DOCS_DIR / "autofix-policy.md")


def test_minimal_real_configs_internal_links_resolve():
    _assert_internal_links_resolve(DOCS_DIR / "minimal-real-configs.md")
