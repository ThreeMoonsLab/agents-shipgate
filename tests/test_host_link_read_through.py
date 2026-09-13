"""#700, step two: an in-tree link at a boundary path is read through.

#659 measured 13 of its 14 remaining refusals as `unreadable` links, and most
were exactly this shape: `CLAUDE.md -> AGENTS.md`, `.claude/skills -> ../.agents/skills`.
By owner decision, a link that resolves inside the repository, at a boundary
location, is read at its target and published under its own path, with
`resolved_through` naming every hop. Anything the resolution cannot vouch for
stays a coverage limit: an external, escaping or dangling target, a link inside
a linked directory, a link into its own ancestor, a chain past the hop bound,
or a directory link that could only hide a `**/` match.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_grants import (
    build_host_drift_payload,
    build_host_grants_baseline,
    host_audit_inventory,
)
from agents_shipgate.schemas.host_grants import HostGrantsBaselineV4

pytestmark = pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")

SKILL = "---\nname: helper\ndescription: A helper.\n---\n\nBody.\n"


def _settings(root: Path, allow: list[str]) -> None:
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": allow}}), encoding="utf-8"
    )


def _blocking(inventory: dict) -> list[tuple[str, str]]:
    return [(item["kind"], item["source"]) for item in inventory["issues"] if item["blocking"]]


def _artifacts(inventory: dict, path: str) -> list[dict]:
    return [item for item in inventory["artifacts"] if item["path"] == path]


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


# --- what is read through -------------------------------------------------------


def test_a_linked_instruction_file_is_read_at_its_target(tmp_path: Path) -> None:
    _settings(tmp_path, ["Read(**)"])
    (tmp_path / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")

    inventory = host_audit_inventory(tmp_path)

    assert _blocking(inventory) == []
    linked = _artifacts(inventory, "CLAUDE.md")
    assert linked and all(item["parse_status"] == "parsed" for item in linked)
    assert all(item["resolved_through"] == ["AGENTS.md"] for item in linked)
    # An ordinary artifact carries no field for it at all.
    assert all("resolved_through" not in item for item in _artifacts(inventory, "AGENTS.md"))


def test_a_linked_skills_directory_is_read_under_the_link(tmp_path: Path) -> None:
    _settings(tmp_path, ["Read(**)"])
    (tmp_path / ".agents" / "skills" / "helper").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "helper" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (tmp_path / ".claude" / "skills").symlink_to("../.agents/skills", target_is_directory=True)

    inventory = host_audit_inventory(tmp_path)

    assert _blocking(inventory) == []
    skill = _artifacts(inventory, ".claude/skills/helper/SKILL.md")
    assert skill, [item["path"] for item in inventory["artifacts"]]
    assert all(item["resolved_through"] == [".agents/skills/helper/SKILL.md"] for item in skill)
    assert all((item.get("instruction_structure") or {}).get("status") == "structured" for item in skill)


def test_a_chain_of_in_tree_links_names_every_hop(tmp_path: Path) -> None:
    _settings(tmp_path, ["Read(**)"])
    (tmp_path / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "agents.md").symlink_to("../AGENTS.md")
    (tmp_path / "CLAUDE.md").symlink_to("docs/agents.md")

    inventory = host_audit_inventory(tmp_path)

    assert _blocking(inventory) == []
    assert all(
        item["resolved_through"] == ["docs/agents.md", "AGENTS.md"]
        for item in _artifacts(inventory, "CLAUDE.md")
    )


# --- what still refuses -----------------------------------------------------------


@pytest.mark.parametrize("shape", ["external", "escaping", "dangling", "ancestor", "linked_inside", "skipped_target", "hop_bound"])
def test_what_the_resolution_cannot_vouch_for_stays_a_limit(tmp_path: Path, shape: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _settings(root, ["Read(**)"])
    if shape == "external":
        outside = tmp_path / "outside.md"
        outside.write_text("# outside\n", encoding="utf-8")
        (root / "CLAUDE.md").symlink_to(outside)
        subject = "CLAUDE.md"
    elif shape == "escaping":
        (tmp_path / "outside.md").write_text("# outside\n", encoding="utf-8")
        (root / "CLAUDE.md").symlink_to("../outside.md")
        subject = "CLAUDE.md"
    elif shape == "dangling":
        (root / "CLAUDE.md").symlink_to("missing.md")
        subject = "CLAUDE.md"
    elif shape == "ancestor":
        (root / ".claude" / "skills").symlink_to("..", target_is_directory=True)
        subject = ".claude/skills"
    elif shape == "linked_inside":
        (root / ".agents" / "skills").mkdir(parents=True)
        (root / "elsewhere").mkdir()
        (root / ".agents" / "skills" / "helper").symlink_to("../../elsewhere", target_is_directory=True)
        (root / ".claude" / "skills").symlink_to("../.agents/skills", target_is_directory=True)
        subject = ".claude/skills"
    elif shape == "skipped_target":
        (root / "node_modules" / "skills" / "helper").mkdir(parents=True)
        (root / "node_modules" / "skills" / "helper" / "SKILL.md").write_text(SKILL, encoding="utf-8")
        (root / ".claude" / "skills").symlink_to("../node_modules/skills", target_is_directory=True)
        subject = ".claude/skills"
    else:
        (root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
        previous = "AGENTS.md"
        for index in range(9):
            name = f"hop{index}.md"
            (root / name).symlink_to(previous)
            previous = name
        (root / "CLAUDE.md").symlink_to(previous)
        subject = "CLAUDE.md"

    inventory = host_audit_inventory(root)

    assert ("unreadable", subject) in _blocking(inventory), (shape, _blocking(inventory))


# --- comparison, baseline and drift ---------------------------------------------


def test_a_v0_4_baseline_stays_comparable(tmp_path: Path) -> None:
    """A v0.4 inventory refused every boundary link, so its baseline holds nothing v0.5 reads differently."""

    _settings(tmp_path, ["Read(**)"])
    inventory = host_audit_inventory(tmp_path)
    baseline = build_host_grants_baseline(inventory)
    legacy = HostGrantsBaselineV4.model_validate(
        {**baseline, "host_grants_schema_version": "0.4"}
    ).model_dump(mode="json")

    payload = build_host_drift_payload(baseline=legacy, inventory=inventory, baseline_file="b.json")

    assert payload["comparison_status"] == "comparable"
    assert payload["has_drift"] is False


def test_retargeting_a_linked_boundary_path_is_a_change(tmp_path: Path) -> None:
    _settings(tmp_path, ["Read(**)"])
    (tmp_path / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "other.md").write_text("# other\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")
    baseline = build_host_grants_baseline(host_audit_inventory(tmp_path))

    (tmp_path / "CLAUDE.md").unlink()
    (tmp_path / "CLAUDE.md").symlink_to("docs/other.md")
    payload = build_host_drift_payload(
        baseline=baseline, inventory=host_audit_inventory(tmp_path), baseline_file="b.json"
    )

    retargeted = [
        change for change in payload["artifact_changes"]
        if (change.get("current") or {}).get("path") == "CLAUDE.md"
    ]
    assert retargeted, payload["artifact_changes"]
    assert retargeted[0]["current"]["resolved_through"] == ["docs/other.md"]
    assert retargeted[0]["baseline"]["resolved_through"] == ["AGENTS.md"]


def test_a_scoped_archive_holds_a_boundary_links_target(tmp_path: Path) -> None:
    from agents_shipgate.cli.verify.git import archive_tree
    from agents_shipgate.core.boundary_registry import is_boundary_surface_path

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _settings(repo, ["Read(**)"])
    (repo / ".agents" / "skills" / "helper").mkdir(parents=True)
    (repo / ".agents" / "skills" / "helper" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (repo / ".claude" / "skills").symlink_to("../.agents/skills", target_is_directory=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "agents.md").write_text("# agents\n", encoding="utf-8")
    (repo / "CLAUDE.md").symlink_to("docs/agents.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    archive = tmp_path / "archive"

    archive_tree(repo, "HEAD", archive, scope=is_boundary_surface_path)

    assert (archive / "docs" / "agents.md").read_text(encoding="utf-8") == "# agents\n"
    assert (archive / ".agents" / "skills" / "helper" / "SKILL.md").read_text(encoding="utf-8") == SKILL


def test_diff_reads_links_through_on_both_sides(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _settings(repo, ["Read"])
    (repo / ".agents" / "skills" / "helper").mkdir(parents=True)
    (repo / ".agents" / "skills" / "helper" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (repo / ".claude" / "skills").symlink_to("../.agents/skills", target_is_directory=True)
    (repo / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _settings(repo, ["Read", "Bash(*)"])

    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", "main", "--json"])
    payload = json.loads(result.stdout)

    assert payload["comparison_status"] == "comparable", payload
    assert [(row["subject"], row["after"]) for row in payload["rows"]] == [
        ("claude-code .claude/settings.json", "Bash(*)")
    ]
