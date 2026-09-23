"""#822: an unchanged limit reached through an in-tree link is named, not a veto.

A `SKILL.md` whose `metadata` holds a non-string value is an `unsupported`
limit this entry cannot resolve. Untouched by a change, it is named as an
unchanged limit and the rest is compared (#721). Reached through an in-tree
link the reader reads through (#700) — `.claude/skills -> ../.agents/skills`,
or a per-skill link — the same untouched file refused the whole comparison
instead, hiding a removed `deny` rule beside it: the unchanged proof asked
whether the link-resolved path was a regular file in Git, and a path through a
link never is.

The proof now follows the link as the reader does, from Git tree entries on
both sides, and holds only when the link (its entry type and text at each
link component) and the file it lands on (its blob) are both unchanged.
Anything else still refuses: a skill added or edited behind the link, the link
retargeted or its text rewritten, a link replaced by a directory or the
reverse, and every link the reader does not read through (dangling, looping,
external, escaping, past the hop bound). The metadata value is never coerced:
`internal: true` stays an `unsupported` structure (#811).

Every route case is a real repository driven through `diff` (text and
`--json`, whose head is the working tree), `verify` (`verifier.json` and its
text, whose head is a commit), the PR comment `verify` writes, and `check`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.git import blob_path_unchanged
from agents_shipgate.core.host_grants import host_audit_inventory

pytestmark = pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")

SETTINGS = ".claude/settings.json"
BASE_SETTINGS = {"permissions": {"allow": ["Read"], "deny": ["Bash(curl *)"]}}
DENY_DROPPED = {"permissions": {"allow": ["Read"]}}
#: The issue's skill: `metadata.internal` is a boolean, which this entry's
#: bounded profile does not accept, so the structure is unresolved.
SKILL = "---\nname: review\ndescription: Review a change.\nmetadata:\n  internal: true\n---\nReview the diff.\n"
#: The issue's control: the same value as a string is accepted.
STRING_SKILL = SKILL.replace("internal: true", 'internal: "true"')
SOURCE = ".claude/skills/review/SKILL.md"
DENY_REMOVED = [("claude-code .claude/settings.json", "Bash(curl *)", "removed")]
NOT_COMPARED = "Not compared: unchanged in this change and not read, so no claim is made about them:"
BOTH = ["base_inventory_incomplete", "head_inventory_incomplete"]
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}

#: Where the skill the host reads at `SOURCE` lives: files, and links by their
#: text. `direct` is the #721 shape every other layout must match.
LAYOUTS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "direct": ({SOURCE: SKILL}, {}),
    "directory-link": (
        {".agents/skills/review/SKILL.md": SKILL},
        {".claude/skills": "../.agents/skills"},
    ),
    "skill-link": ({"skills/review/SKILL.md": SKILL}, {".claude/skills/review": "../../skills/review"}),
    "file-link": ({"shared/review.md": SKILL}, {SOURCE: "../../../shared/review.md"}),
    "link-chain": (
        {"shared/review.md": SKILL},
        {SOURCE: "../../../docs/review.md", "docs/review.md": "../shared/review.md"},
    ),
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _write(repo: Path, name: str, value: object) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _remove(repo: Path, name: str) -> None:
    """Remove what is at ``name`` itself, never following a link there."""

    path = repo / name
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _link(repo: Path, name: str, target: str) -> None:
    _remove(repo, name)
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _repository(
    tmp_path: Path,
    files: dict[str, str],
    links: dict[str, str],
    *,
    head_removed: tuple[str, ...] = (),
    head_files: dict[str, str] | None = None,
    head_links: dict[str, str] | None = None,
    head_settings: object = DENY_DROPPED,
) -> Path:
    """`main` holds the deny rule beside ``files`` and ``links``; branch `change` drops it.

    On `change`, ``head_removed`` paths are removed first, then ``head_files``
    and ``head_links`` are written, and all of it is committed. A link text
    ``{outside}`` names a directory beside the repository that holds the same
    skill, so a link that leaves the repository lands on real content.
    """

    outside = tmp_path / "outside"
    _write(outside, "skills/review/SKILL.md", SKILL)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, SETTINGS, BASE_SETTINGS)
    _write(repo, "README.md", "# demo\n")
    for name, value in files.items():
        _write(repo, name, value)
    for name, target in links.items():
        _link(repo, name, target.format(outside=outside))
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "change")
    _write(repo, SETTINGS, head_settings)
    for name in head_removed:
        _remove(repo, name)
    for name, value in (head_files or {}).items():
        _write(repo, name, value)
    for name, target in (head_links or {}).items():
        _link(repo, name, target.format(outside=outside))
    _commit(repo, "change")
    return repo


def _layout(tmp_path: Path, name: str, **head) -> Path:
    files, links = LAYOUTS[name]
    return _repository(tmp_path, files, links, **head)


def _invoke(args: list[str]) -> str:
    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1", "COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return result.output


def _diff(repo: Path) -> dict:
    return json.loads(_invoke(["diff", "--workspace", str(repo), "--base", "main", "--json"]))


def _all_routes(repo: Path, tmp_path: Path) -> tuple[str, dict, str, str]:
    """diff text and JSON, and verify's comparison, text and PR comment, agreeing on one comparison."""

    command = ["diff", "--workspace", str(repo), "--base", "main"]
    text, payload = _invoke(command), json.loads(_invoke([*command, "--json"]))
    out = tmp_path / "out"
    verify_text = _invoke([
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--format", "text", "--base", "main",
        "--head", _git(repo, "rev-parse", "HEAD"),
    ])
    comparison = json.loads((out / "verifier.json").read_text(encoding="utf-8"))["host_comparison"]
    for key in ("comparison_status", "incomparable_reasons", "rows", "unchanged_limits", "coverage"):
        assert comparison[key] == payload[key], key
    if payload["review"] is not None:
        # The reproduction names the working tree for `diff` and the commit for
        # `verify`; the presented changes are the same.
        assert comparison["review"]["changes"] == payload["review"]["changes"]
    comment = (out / "pr-comment.md").read_text(encoding="utf-8")
    return text, payload, verify_text, comment


def _rows(payload: dict) -> list[tuple[str, str, str]]:
    return [
        (row["subject"], row["before"] if row["direction"] == "removed" else row["after"], row["direction"])
        for row in payload["rows"]
    ]


def _limits(payload: dict) -> set[tuple[str, str, str]]:
    return {(limit["host"], limit["source"], limit["limit"]) for limit in payload["unchanged_limits"]}


# --- the issue's reproduction: every layout matches `direct` ---------------------


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_an_unchanged_limit_through_an_in_tree_link_is_named_like_a_direct_one(
    tmp_path: Path, layout: str
) -> None:
    repo = _layout(tmp_path, layout)

    text, payload, verify_text, comment = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "comparable"
    assert payload["incomparable_reasons"] == []
    assert _rows(payload) == DENY_REMOVED
    assert ("claude-code", SOURCE, "unsupported") in _limits(payload)
    # Only the skill is a limit; a file the link lands on that a host also
    # reads directly (`.agents/skills/...`) is named on its own path.
    assert {limit["limit"] for limit in payload["unchanged_limits"]} == {"unsupported"}
    assert all(
        "frontmatter_invalid_structure" in limit["detail"] for limit in payload["unchanged_limits"]
    )
    assert NOT_COMPARED in text and f"  claude-code {SOURCE} — unsupported" in text
    assert "Not compared: unchanged in this change" in verify_text
    assert NOT_COMPARED in comment and f"` {SOURCE} `" in comment
    assert "Bash(curl *)" in text and "Bash(curl *)" in comment


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_the_proof_holds_between_commits_and_against_the_working_tree(
    tmp_path: Path, layout: str
) -> None:
    repo = _layout(tmp_path, layout)

    assert blob_path_unchanged(repo, "main", "HEAD", SOURCE)
    assert blob_path_unchanged(repo, "main", None, SOURCE)


def test_a_metadata_value_is_never_coerced(tmp_path: Path) -> None:
    """#811: a boolean stays an unsupported structure through a link, and the
    string the issue used as its control reads as supported, with no limit."""

    boolean = _layout(tmp_path / "boolean", "directory-link")
    string = _repository(
        tmp_path / "string",
        {".agents/skills/review/SKILL.md": STRING_SKILL},
        {".claude/skills": "../.agents/skills"},
    )

    assert ("claude-code", SOURCE, "unsupported") in _limits(_diff(boolean))
    controlled = _diff(string)
    assert controlled["comparison_status"] == "comparable"
    assert controlled["unchanged_limits"] == []
    assert _rows(controlled) == DENY_REMOVED


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_check_refuses_exactly_as_it_does_for_a_direct_limit(tmp_path: Path, layout: str) -> None:
    """`check`'s boundary result cannot name a limit (#721), so it refuses its
    comparison with the reason a direct limit gives; its decision comes from its
    own routing and does not move."""

    repo = _layout(tmp_path, layout)

    payload = json.loads(_invoke([
        "check", "--workspace", str(repo), "--base", "main", "--head", "HEAD",
        "--format", "agent-boundary-json",
    ]))

    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == ["unchanged_limits_not_representable"]
    assert payload["rows"] == []
    assert payload["decision"] == "require_review"
    assert "HOST-PERMISSION-DENY-REMOVED" in [item["id"] for item in payload["violations"]]


# --- negative controls: what the change touched still refuses ---------------------

EDITED = SKILL.replace("Review the diff.", "Review it again.")
#: ``(base files, base links, what the head changes, reasons)``. Each head also
#: drops the deny rule, so a comparison that compared past the limit would
#: publish that row.
CHANGED: dict[str, tuple[dict[str, str], dict[str, str], dict[str, object], list[str]]] = {
    "skill-added-behind-the-link": (
        {".agents/skills/other/SKILL.md": "---\nname: other\ndescription: d\n---\nbody\n"},
        {".claude/skills": "../.agents/skills"},
        {"head_files": {".agents/skills/review/SKILL.md": SKILL}},
        ["head_inventory_incomplete"],
    ),
    "skill-edited-behind-the-link": (
        *LAYOUTS["directory-link"],
        {"head_files": {".agents/skills/review/SKILL.md": EDITED}},
        BOTH,
    ),
    # The same bytes behind another link text: the link changed, so the proof
    # cannot say what the reader opens is what it opened before.
    "link-retargeted-to-an-identical-copy": (
        {"skills/review/SKILL.md": SKILL, "copy/review/SKILL.md": SKILL},
        {".claude/skills/review": "../../skills/review"},
        {"head_links": {".claude/skills/review": "../../copy/review"}},
        BOTH,
    ),
    "link-text-rewritten-to-land-on-the-same-file": (
        *LAYOUTS["skill-link"],
        {"head_links": {".claude/skills/review": "../../skills/./review"}},
        BOTH,
    ),
    "link-replaced-by-a-directory-with-the-same-bytes": (
        *LAYOUTS["skill-link"],
        {"head_removed": (".claude/skills/review",), "head_files": {SOURCE: SKILL}},
        BOTH,
    ),
    "directory-replaced-by-a-link-to-the-same-bytes": (
        {SOURCE: SKILL, "skills/review/SKILL.md": SKILL},
        {},
        {"head_links": {".claude/skills/review": "../../skills/review"}},
        BOTH,
    ),
    "file-link-target-edited": (
        *LAYOUTS["file-link"],
        {"head_files": {"shared/review.md": EDITED}},
        BOTH,
    ),
    "second-hop-retargeted": (
        {**LAYOUTS["link-chain"][0], "shared/copy.md": SKILL},
        LAYOUTS["link-chain"][1],
        {"head_links": {"docs/review.md": "../shared/copy.md"}},
        BOTH,
    ),
}


@pytest.mark.parametrize("case", list(CHANGED))
def test_a_change_to_the_link_or_what_it_lands_on_still_refuses(tmp_path: Path, case: str) -> None:
    files, links, head, reasons = CHANGED[case]
    repo = _repository(tmp_path, files, links, **head)

    _text, payload, _verify_text, _comment = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == reasons
    assert payload["rows"] == [] and payload["unchanged_limits"] == []
    assert not blob_path_unchanged(repo, "main", "HEAD", SOURCE)
    assert not blob_path_unchanged(repo, "main", None, SOURCE)


def test_a_working_tree_change_to_the_link_or_its_target_is_not_unchanged(tmp_path: Path) -> None:
    """`diff` reads the working tree: an uncommitted retarget or edit refuses as a committed one does."""

    repo = _layout(tmp_path, "skill-link")
    assert blob_path_unchanged(repo, "main", None, SOURCE)

    _write(repo, "copy/review/SKILL.md", SKILL)
    _link(repo, ".claude/skills/review", "../../copy/review")
    assert not blob_path_unchanged(repo, "main", None, SOURCE)
    assert _diff(repo)["comparison_status"] == "incomparable"

    _link(repo, ".claude/skills/review", "../../skills/review")
    assert blob_path_unchanged(repo, "main", None, SOURCE)
    _write(repo, "skills/review/SKILL.md", EDITED)
    assert not blob_path_unchanged(repo, "main", None, SOURCE)
    assert _diff(repo)["comparison_status"] == "incomparable"


# --- the proof follows exactly the links the reader reads through ------------------


def _chain(length: int) -> tuple[dict[str, str], dict[str, str]]:
    """``SOURCE`` reaching the skill through ``length`` file links in a row."""

    links = {f"hop{index}.md": f"hop{index - 1}.md" for index in range(1, length - 1)}
    links["hop0.md"] = "shared/review.md"
    links[SOURCE] = f"../../../hop{length - 2}.md"
    return {"shared/review.md": SKILL}, links


#: Shapes the reader does or does not read `SOURCE` through, unchanged between
#: the commits. ``True`` where the reader reads the skill at `SOURCE`.
READER_SHAPES: dict[str, tuple[dict[str, str], dict[str, str], bool]] = {
    **{name: (*LAYOUTS[name], True) for name in LAYOUTS},
    "eight-links-in-a-row": (*_chain(8), True),
    "nine-links-in-a-row": (*_chain(9), False),
    "dangling": ({}, {".claude/skills": "../.agents/skills"}, False),
    "loop": ({}, {".claude/skills": "../.agents/skills", ".agents/skills": "../.claude/skills"}, False),
    "escaping": ({}, {".claude/skills": "../../outside/skills"}, False),
    "external": ({}, {".claude/skills": "{outside}/skills"}, False),
    # A link as an intermediate component of where the first link lands.
    "link-above-the-target": (
        {".agents/skills/review/SKILL.md": SKILL},
        {".claude/skills": "../alias/skills", "alias": ".agents"},
        False,
    ),
    # A link inside a linked directory: the reader does not read through it.
    "link-inside-the-linked-directory": (
        {"skills/review/SKILL.md": SKILL},
        {".claude/skills": "../.agents/skills", ".agents/skills/review": "../../skills/review"},
        False,
    ),
}


@pytest.mark.parametrize("shape", list(READER_SHAPES))
def test_the_proof_holds_exactly_where_the_reader_reads_through(tmp_path: Path, shape: str) -> None:
    files, links, reads_through = READER_SHAPES[shape]
    repo = _repository(tmp_path, files, links, head_settings=BASE_SETTINGS)

    issues = host_audit_inventory(repo)["issues"]
    read = any(issue["source"] == SOURCE and issue["kind"] == "unsupported" for issue in issues)

    assert read is reads_through, [(issue["kind"], issue["source"]) for issue in issues]
    assert blob_path_unchanged(repo, "main", "HEAD", SOURCE) is reads_through
    assert blob_path_unchanged(repo, "main", None, SOURCE) is reads_through


@pytest.mark.parametrize("shape", [name for name, (*_, reads) in READER_SHAPES.items() if not reads])
def test_a_link_the_reader_does_not_read_through_still_refuses(tmp_path: Path, shape: str) -> None:
    """Unchanged, and still refused: the reader read nothing behind it on either side."""

    files, links, _reads_through = READER_SHAPES[shape]
    repo = _repository(tmp_path, files, links)

    payload = _diff(repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["rows"] == [] and payload["unchanged_limits"] == []
    assert "unreadable" in {item["limit"] for item in payload["coverage"]["items"]}
