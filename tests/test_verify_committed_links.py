"""``verify`` reads a committed tree that holds links as a checkout of it reads.

A repository that commits ``CLAUDE.md -> AGENTS.md`` made ``verify --head``
(and so every GitHub Action run) exit 2 with ``unsupported external binding``,
and a worktree run lose its base comparison, because the full verification
archive refused every link. A link that opens a file or directory inside the
tree is now recreated as the link it is, so the archived scan meets what the
worktree scan meets. A link that leaves the tree keeps the refusal.
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

pytestmark = pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")

runner = CliRunner()

MANIFEST = """\
version: "0.1"
project:
  name: test
agent:
  name: test-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
"""

TOOLS = '{"tools":[{"name":"docs.lookup","annotations":{"readOnlyHint":true}}]}\n'

#: What differs between an archived-head run and a worktree run of the same
#: commit whatever the tree holds: run identities, where the reports were
#: written, and how the cached base report's path is spelled.
_RUN_SPECIFIC = {"decision_id", "generated_reports", "input_set_id", "request_id", "subject_id"}


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "shipgate.yaml").write_text(MANIFEST, encoding="utf-8")
    (repo / "tools.json").write_text(TOOLS, encoding="utf-8")
    _commit(repo, "base")
    return repo


def _commit_links(repo: Path, links: dict[str, str]) -> None:
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (repo / ".agents/skills/demo").mkdir(parents=True)
    (repo / ".agents/skills/demo/SKILL.md").write_text(
        "---\nname: demo\ndescription: A demo skill.\n---\nBody.\n", encoding="utf-8"
    )
    for link, target in links.items():
        (repo / link).parent.mkdir(parents=True, exist_ok=True)
        (repo / link).symlink_to(target)
    _commit(repo, "links")
    # A later change, so `--base HEAD~1` is a base that already holds the links.
    (repo / "README.md").write_text("change\n", encoding="utf-8")
    _commit(repo, "change")


def _verify(repo: Path, out: Path, *args: str):
    return runner.invoke(
        app, ["verify", "--workspace", str(repo), "--json", "--out", str(out), *args]
    )


def _error_line(result) -> str:
    return next(line for line in result.stderr.splitlines() if line.startswith(("Config", "Input")))


def _comparable(report: dict) -> dict:
    report = {key: value for key, value in report.items() if key not in _RUN_SPECIFIC}
    for key in ("action_surface_diff", "tool_surface_diff"):
        section = report.get(key)
        if isinstance(section, dict) and isinstance(section.get("base"), dict):
            base = {k: v for k, v in section["base"].items() if k != "path"}
            report[key] = {**section, "base": base}
    return report


@pytest.mark.parametrize(
    "links",
    [{"CLAUDE.md": "AGENTS.md"}, {".claude/skills": "../.agents/skills"}],
    ids=["claude-md", "skills-directory"],
)
def test_verify_reads_committed_in_tree_links_as_a_checkout_does(tmp_path, links):
    repo = _repo(tmp_path)
    _commit_links(repo, links)

    runs = {}
    for label, args in {
        "archived": ("--base", "HEAD~1", "--head", "HEAD"),
        "worktree": ("--base", "HEAD~1"),
    }.items():
        out = tmp_path / f"out-{label}"
        result = _verify(repo, out, *args)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        # The base tree holds the links too, so the comparison is kept.
        assert payload["base_status"] == "succeeded", payload["base_notes"]
        assert payload["head_status"] == "succeeded"
        runs[label] = (payload, json.loads((out / "report.json").read_text(encoding="utf-8")))

    (archived, archived_report), (worktree, worktree_report) = runs["archived"], runs["worktree"]
    assert archived["merge_verdict"] == worktree["merge_verdict"]
    assert archived["decision"] == worktree["decision"]
    assert _comparable(archived_report) == _comparable(worktree_report)

    prepared = runner.invoke(app, [
        "verification", "prepare", "--workspace", str(repo), "--base", "HEAD~1",
        "--head", "HEAD", "--out", str(tmp_path / "plan.json"),
    ])
    assert prepared.exit_code == 0, prepared.output


@pytest.mark.parametrize("target", ["../outside", "/etc/hosts"], ids=["escaping", "absolute"])
def test_verify_still_refuses_a_committed_link_that_leaves_the_tree(tmp_path, target):
    repo = _repo(tmp_path)
    _commit_links(repo, {"VERSION": target})

    archived = _verify(repo, tmp_path / "out-archived", "--base", "HEAD~1", "--head", "HEAD")
    assert archived.exit_code == 2, archived.output
    error = _error_line(archived)
    assert error == (
        "Config error: Git tree contains unsupported external binding at VERSION "
        "(mode 120000, type blob): only a link that opens a file or directory inside "
        "the repository is recreated."
    )
    assert "shipgate.yaml" not in error

    worktree = _verify(repo, tmp_path / "out-worktree", "--base", "HEAD~1")
    assert worktree.exit_code == 0, worktree.output
    payload = json.loads(worktree.stdout)
    assert payload["base_status"] == "archive_failed"
    assert "unsupported external binding at VERSION " in payload["base_notes"][0]


def test_verify_meets_a_committed_tool_source_link_as_a_checkout_does(tmp_path):
    # The manifest's tool source becomes an in-tree link to the moved file.
    # The identity-bound reader refuses to read through it on a checkout, and
    # the archived head now presents the same link, so it refuses it the same
    # way, instead of refusing the whole tree before any reader runs.
    repo = _repo(tmp_path)
    (repo / "inventories").mkdir()
    (repo / "tools.json").rename(repo / "inventories/tools.json")
    (repo / "tools.json").symlink_to("inventories/tools.json")
    _commit(repo, "move the tool source behind a link")

    worktree = _verify(repo, tmp_path / "out-worktree", "--base", "HEAD~1")
    archived = _verify(repo, tmp_path / "out-archived", "--base", "HEAD~1", "--head", "HEAD")
    assert worktree.exit_code != 0, worktree.output
    assert (archived.exit_code, _error_line(archived)) == (
        worktree.exit_code, _error_line(worktree)
    )
    assert "symlink" in _error_line(archived)

    # It is the committed link the archived run meets: a worktree that has
    # since replaced it with the file changes nothing about `--head HEAD`.
    (repo / "tools.json").unlink()
    shutil.copyfile(repo / "inventories/tools.json", repo / "tools.json")
    again = _verify(repo, tmp_path / "out-again", "--base", "HEAD~1", "--head", "HEAD")
    assert (again.exit_code, _error_line(again)) == (archived.exit_code, _error_line(archived))
