"""#1110: an instruction file under `.github/workflows/` is not a workflow.

Codex reads a nested `AGENTS.md` and Claude Code a nested `CLAUDE.md`, and the
boundary registry enumerates both there through `**/AGENTS.md` and
`**/CLAUDE.md`. The repository reader filed every source under
`.github/workflows/` as a GitHub workflow before its Markdown check ran, so it
parsed the Markdown as a workflow document and recorded `parse_failed` under
host `github`. On `pydantic/pydantic-ai` PR #12653 that refused both
inventories, and the comparison came back `incomparable` with no rows, hiding
the PR's real workflow changes. Where the Markdown was unchanged, the
comparison held but still listed the file as not compared, `parse_failed`.

GitHub runs only `.yml`/`.yaml` directly in the root `.github/workflows/`. That
is what the registry's root workflow globs enumerate and what `check` already
evaluated for these files, so only those are workflows here too. Anything else the registry
finds in that directory is read by its own adapter, as it would be anywhere
else in the tree: the instruction files keep their adapters' hosts.

Every route case is a real two-commit repository driven through `diff` (text
and `--json`), `verify --preview`, `audit --host --drift` against a baseline
saved at the base commit, and `check`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.boundary_registry import (
    boundary_adapters_for_path,
    is_github_workflow_path,
)
from agents_shipgate.core.host_boundary import _is_workflow_path
from agents_shipgate.core.host_grants import (
    _audit_hosts,
    _source_kind,
    local_reusable_target,
)

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
WORKFLOWS = ".github/workflows"
READ = (
    "on: [pull_request]\npermissions:\n  contents: read\n"
    "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n"
)
WRITE = READ.replace("contents: read", "contents: write")
NOTES = "# Workflow notes\n\nKeep every job's token read-only.\n"
EDITED_NOTES = NOTES + "\nPin every action to a commit.\n"
SHARED_HOSTS = ("claude-code", "codex", "cursor")
#: The hosts the registry attributes each instruction file to.
INSTRUCTION_HOSTS = {"AGENTS.md": SHARED_HOSTS, "CLAUDE.md": ("claude-code",)}


def _hosts(path: str) -> set[str]:
    """Every host the repository reader files ``path`` under."""

    return {
        host
        for adapter in boundary_adapters_for_path(path)
        for host in _audit_hosts(adapter.id, path, adapter.hosts)
    }


@pytest.mark.parametrize(
    ("path", "kind", "hosts"),
    [
        (f"{WORKFLOWS}/ci.yml", "workflow", ("github",)),
        (f"{WORKFLOWS}/release.yaml", "workflow", ("github",)),
        # Classification has always folded case, as the registry does.
        (".GitHub/Workflows/CI.YML", "workflow", ("github",)),
        (f"{WORKFLOWS}/AGENTS.md", "instructions", SHARED_HOSTS),
        (f"{WORKFLOWS}/CLAUDE.md", "instructions", ("claude-code",)),
        (f"{WORKFLOWS}/Agents.md", "instructions", SHARED_HOSTS),
        (f"{WORKFLOWS}/shared/AGENTS.md", "instructions", SHARED_HOSTS),
        (f"{WORKFLOWS}/.mcp.json", "mcp", ("claude-code",)),
        # A nested copy never runs. It keeps the handling it already had.
        (f"samples/x/{WORKFLOWS}/ci.yml", "config", SHARED_HOSTS),
    ],
)
def test_only_a_root_workflow_document_is_a_workflow(path, kind, hosts):
    assert boundary_adapters_for_path(path), "the registry enumerates every case"
    assert _source_kind(path) == kind
    assert _hosts(path) == set(hosts)
    assert is_github_workflow_path(path) is (kind == "workflow")


@pytest.mark.parametrize(
    "path",
    [
        f"{WORKFLOWS}/ci.yml",
        f"{WORKFLOWS}/release.yaml",
        f"{WORKFLOWS}/AGENTS.md",
        f"{WORKFLOWS}/CLAUDE.md",
        f"{WORKFLOWS}/shared/AGENTS.md",
        f"samples/x/{WORKFLOWS}/ci.yml",
    ],
)
def test_the_reader_and_check_agree_on_what_is_a_workflow(path):
    """`check` already evaluated only these as workflows; `diff` now reads the same set."""

    assert (_source_kind(path) == "workflow") is _is_workflow_path(path)


def test_a_markdown_file_is_never_followed_as_a_reusable_workflow():
    assert local_reusable_target(f"./{WORKFLOWS}/AGENTS.md", f"{WORKFLOWS}/ci.yml") == ""
    assert local_reusable_target(f"./{WORKFLOWS}/deploy.yml", f"{WORKFLOWS}/ci.yml") == (
        f"{WORKFLOWS}/deploy.yml"
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    )


def _invoke(args: list[str]) -> str:
    return CliRunner().invoke(app, args).output


def _write(repo: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text, encoding="utf-8")


def _repository(
    tmp_path: Path,
    base: dict[str, str],
    head: dict[str, str],
    *,
    links: dict[str, str] | None = None,
) -> Path:
    """A base commit with a saved host-grants baseline, then the change.

    ``links`` are file links, by their text, committed in the base and left
    untouched by the change.
    """

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, {".gitignore": "agents-shipgate-reports/\n.agents-shipgate/\n", **base})
    for link, target in (links or {}).items():
        (repo / link).symlink_to(target)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _invoke(["audit", "--host", "--workspace", str(repo), "--save-baseline", "--json"])
    _git(repo, "checkout", "-q", "-b", "change")
    _write(repo, head)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    return repo


def _routes(repo: Path) -> dict:
    workspace = ["--workspace", str(repo), "--base", "main"]
    return {
        "text": _invoke(["diff", *workspace]),
        "diff": json.loads(_invoke(["diff", *workspace, "--json"])),
        "verify": json.loads(
            _invoke(["verify", "--preview", *workspace, "--head", "HEAD", "--json"])
        )["host_comparison"],
        "drift": json.loads(
            _invoke(["audit", "--host", "--workspace", str(repo), "--drift", "--json"])
        ),
        "check": json.loads(
            _invoke(
                ["check", "--agent", "claude-code", *workspace, "--format", "agent-boundary-json"]
            )
        ),
        "inventory": json.loads(_invoke(["audit", "--host", "--workspace", str(repo), "--json"])),
    }


def _widened(workflow: str) -> list[tuple[str, str, bool]]:
    return [(f"github {WORKFLOWS}/{workflow}", "widened", True)]


def _rows(rows: list[dict]) -> list[tuple[str, str, bool]]:
    return sorted((row["subject"], row["direction"], row["expands"]) for row in rows)


@pytest.mark.parametrize(("name", "workflow"), [("AGENTS.md", "ci.yml"), ("CLAUDE.md", "release.yaml")])
@pytest.mark.parametrize("edited", [False, True], ids=["unchanged", "changed"])
def test_an_instruction_file_beside_a_changed_workflow_is_compared(
    tmp_path, workflow, name, edited
):
    instructions = f"{WORKFLOWS}/{name}"
    routes = _routes(
        _repository(
            tmp_path,
            {f"{WORKFLOWS}/{workflow}": READ, instructions: NOTES},
            {f"{WORKFLOWS}/{workflow}": WRITE, **({instructions: EDITED_NOTES} if edited else {})},
        )
    )
    diff = routes["diff"]
    assert diff["comparison_status"] == "comparable", diff["incomparable_reasons"]
    assert diff["incomparable_reasons"] == []
    assert diff["unchanged_limits"] == []
    # The workflow change the Markdown hid is a row again, on every route.
    assert _rows(diff["rows"]) == _widened(workflow)
    assert routes["verify"]["comparison_status"] == "comparable"
    assert _rows(routes["verify"]["rows"]) == _widened(workflow)
    assert _rows(routes["check"]["rows"]) == _widened(workflow)
    assert routes["drift"]["comparison_status"] == "comparable"
    assert routes["drift"]["expansion_signals"] == [
        f"workflow_write_changed: {WORKFLOWS}/{workflow}"
    ]
    violations = [item["id"] for item in routes["check"]["violations"]]
    assert "HOST-WORKFLOW-PERMISSIONS-EXPANDED" in violations
    assert "BOUNDARY-INPUT-INCOMPLETE" not in violations
    # Nothing was read as a workflow it is not, so nothing is listed as unread.
    assert "parse_failed" not in routes["text"]
    assert "Not compared" not in routes["text"]
    assert all(item["limit"] is None for item in diff["coverage"]["items"])
    inventory = routes["inventory"]
    assert inventory["issues"] == []
    assert sorted(
        (artifact["host"], artifact["kind"], artifact["parse_status"])
        for artifact in inventory["artifacts"]
        if artifact["path"] == instructions
    ) == [(host, "instructions", "parsed") for host in INSTRUCTION_HOSTS[name]]
    # The ordinary instruction source it is anywhere else: compared under its
    # adapter's hosts, never `github`. Prose is guidance, so an edit to it is
    # a changed source with no grant row, as for a nested `AGENTS.md`.
    assert [
        (item["status"], tuple(item["hosts"]))
        for item in diff["coverage"]["items"]
        if item["source"] == instructions
    ] == [("changed_without_grant_change" if edited else "compared", INSTRUCTION_HOSTS[name])]


def test_a_workflow_directory_instruction_reads_as_one_anywhere_else(tmp_path):
    """The same prose edit to `AGENTS.md` reads alike under `.github/workflows/` and `docs/`."""

    items = {}
    for directory in (WORKFLOWS, "docs"):
        source = f"{directory}/AGENTS.md"
        diff = _routes(
            _repository(
                tmp_path / directory.replace("/", "-"),
                {f"{WORKFLOWS}/ci.yml": READ, source: NOTES},
                {f"{WORKFLOWS}/ci.yml": WRITE, source: EDITED_NOTES},
            )
        )["diff"]
        assert _rows(diff["rows"]) == _widened("ci.yml")
        (items[directory],) = (
            {key: value for key, value in item.items() if key != "source"}
            for item in diff["coverage"]["items"]
            if item["source"] == source
        )
    assert items[WORKFLOWS] == items["docs"]


@pytest.mark.skipif(os.name == "nt", reason="symbolic link fixture")
def test_a_claude_md_link_to_agents_md_is_read_through_as_anywhere_else(tmp_path):
    """The public shape: `CLAUDE.md` is an unchanged link to the `AGENTS.md` beside it.

    The link is read at its target (#700), under Claude Code only. Its target
    changed, so the link is not proven unchanged, which limits nothing: the
    comparison holds, the workflow row is published, and the link reads as a
    `docs/CLAUDE.md` link to `docs/AGENTS.md` does.
    """

    items = {}
    for directory in (WORKFLOWS, "docs"):
        agents, claude = f"{directory}/AGENTS.md", f"{directory}/CLAUDE.md"
        routes = _routes(
            _repository(
                tmp_path / directory.replace("/", "-"),
                {f"{WORKFLOWS}/ci.yml": READ, agents: NOTES},
                {f"{WORKFLOWS}/ci.yml": WRITE, agents: EDITED_NOTES},
                links={claude: "AGENTS.md"},
            )
        )
        diff = routes["diff"]
        assert diff["comparison_status"] == "comparable", diff["incomparable_reasons"]
        assert diff["unchanged_limits"] == []
        assert _rows(diff["rows"]) == _widened("ci.yml") == _rows(routes["verify"]["rows"])
        assert _rows(routes["check"]["rows"]) == _widened("ci.yml")
        assert routes["inventory"]["issues"] == []
        assert [
            (artifact["host"], artifact["kind"], artifact["parse_status"], artifact["resolved_through"])
            for artifact in routes["inventory"]["artifacts"]
            if artifact["path"] == claude
        ] == [("claude-code", "instructions", "parsed", [agents])]
        items[directory] = sorted(
            (item["source"].rsplit("/", 1)[-1], item["status"], tuple(item["hosts"]), item["limit"])
            for item in diff["coverage"]["items"]
            if item["source"] in {agents, claude}
        )
    assert items[WORKFLOWS] == items["docs"] == [
        ("AGENTS.md", "changed_without_grant_change", SHARED_HOSTS, None),
        ("CLAUDE.md", "unchanged_not_proven", ("claude-code",), None),
    ]


def test_markdown_alone_changing_beside_an_unchanged_workflow_yields_no_workflow_row(tmp_path):
    routes = _routes(
        _repository(
            tmp_path,
            {f"{WORKFLOWS}/ci.yml": READ, f"{WORKFLOWS}/AGENTS.md": NOTES},
            {f"{WORKFLOWS}/AGENTS.md": EDITED_NOTES},
        )
    )
    diff = routes["diff"]
    assert diff["comparison_status"] == "comparable"
    assert diff["rows"] == [] and routes["verify"]["rows"] == []
    assert routes["drift"]["expansion_signals"] == []
    assert [
        (item["status"], tuple(item["hosts"]))
        for item in diff["coverage"]["items"]
        if item["source"] == f"{WORKFLOWS}/AGENTS.md"
    ] == [("changed_without_grant_change", SHARED_HOSTS)]
