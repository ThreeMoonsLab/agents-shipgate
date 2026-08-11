"""Which directory one ``shipgate.yaml`` describes (issue #363).

Reproduces the monorepo shape that made ``verify --preview`` route
adoption to the repository root: several self-contained agent projects
under one repository, and a pull request that touches exactly one of
them. A root-scoped manifest there declares one ``agent.name`` for
dozens of unrelated agents, so the alignment layer it feeds has nothing
left to align.

Three behaviors are pinned here:

* preview suggests the project the diff actually touches, and re-routes
  to ``verify`` once that project carries its own manifest — otherwise
  following the suggestion once turns the second preview into a loop;
* ``init --write`` refuses a workspace whose agents live in more than
  one project rather than adopting the first agent name it parsed, and
  a refused run writes nothing at all;
* the ordinary single-project workspace is untouched by all of it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.discovery import detect_workspace
from agents_shipgate.cli.discovery.scope import resolve_change_scope
from agents_shipgate.cli.main import app

runner = CliRunner()

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

_ADK_AGENT_MODULE = """\
from google.adk.agents import Agent


def {tool}(target: str) -> str:
    \"\"\"Do the thing.\"\"\"
    return "done"


root_agent = Agent(name="{name}", tools=[{tool}])
"""

_PYPROJECT = """\
[project]
name = "{name}"
version = "0.1.0"
dependencies = ["google-adk"]
"""


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    return repo


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _set_origin_main(repo: Path) -> None:
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )


def _write_agent_project(root: Path, relative: str, *, name: str, tool: str) -> Path:
    project = root / relative
    (project / "app").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        _PYPROJECT.format(name=name.replace("_", "-")), encoding="utf-8"
    )
    (project / "app" / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name=name, tool=tool), encoding="utf-8"
    )
    return project


@pytest.fixture
def monorepo(tmp_path: Path) -> Path:
    """Two agent projects, each its own project root, in one repository."""

    repo = _init_repo(tmp_path)
    _write_agent_project(
        repo, "python/agents/crypto-payroll-agent", name="crypto_payroll_agent", tool="pay"
    )
    _write_agent_project(repo, "python/agents/RAG", name="ask_rag_agent", tool="ask")
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (repo / "README.md").write_text("# monorepo\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    return repo


def _touch_one_project(repo: Path) -> None:
    """A pull request that changes exactly one of the two projects."""

    target = repo / "python/agents/crypto-payroll-agent/app/agent.py"
    target.write_text(
        target.read_text(encoding="utf-8")
        + '\n\ndef refund(target: str) -> str:\n    """Refund."""\n    return "refunded"\n',
        encoding="utf-8",
    )
    _commit_all(repo, "add refund tool")


def _preview(repo: Path, *, workspace: Path | None = None) -> dict:
    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(workspace or repo),
            "--preview",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# --- resolve_change_scope ---------------------------------------------------


def test_scope_is_the_project_root_above_the_changed_paths(monorepo: Path) -> None:
    scope = resolve_change_scope(
        root=monorepo,
        changed_files=[
            "python/agents/crypto-payroll-agent/app/agent.py",
            "python/agents/crypto-payroll-agent/README.md",
        ],
    )

    assert scope is not None
    assert scope.relative == "python/agents/crypto-payroll-agent"
    assert scope.marker == "pyproject.toml"


def test_scope_declines_when_the_change_spans_two_projects(monorepo: Path) -> None:
    """Two projects share only the repository root, which is the scope we
    already have — narrowing to ``python/agents`` would name a directory
    that is not a project at all."""

    assert (
        resolve_change_scope(
            root=monorepo,
            changed_files=[
                "python/agents/crypto-payroll-agent/app/agent.py",
                "python/agents/RAG/app/agent.py",
            ],
        )
        is None
    )


def test_scope_declines_for_a_root_level_change(monorepo: Path) -> None:
    assert resolve_change_scope(root=monorepo, changed_files=["README.md"]) is None


def test_a_doc_beside_the_project_does_not_veto_the_scope(monorepo: Path) -> None:
    """The shape of the reported pull request: it edits the index README one
    directory above the project it adds. That README belongs to the
    repository root, and reading it as a competing claim would send the
    answer straight back to the root — which is the routing #363 is about."""

    (monorepo / "pyproject.toml").write_text(
        "[project]\nname = 'samples'\n", encoding="utf-8"
    )

    scope = resolve_change_scope(
        root=monorepo,
        changed_files=[
            "python/agents/README.md",
            "python/agents/crypto-payroll-agent/app/agent.py",
            "python/agents/crypto-payroll-agent/pyproject.toml",
        ],
    )

    assert scope is not None
    assert scope.relative == "python/agents/crypto-payroll-agent"


def test_a_capability_bearing_root_file_does_veto_the_scope(monorepo: Path) -> None:
    """A doc carries no capability surface; a Python module at the root of a
    root-level project does. Two projects are then in play and the answer
    narrows to nothing rather than to the deeper one."""

    (monorepo / "pyproject.toml").write_text(
        "[project]\nname = 'samples'\n", encoding="utf-8"
    )

    assert (
        resolve_change_scope(
            root=monorepo,
            changed_files=[
                "shared/router.py",
                "python/agents/crypto-payroll-agent/app/agent.py",
            ],
        )
        is None
    )


def test_a_prompt_is_not_read_as_documentation(monorepo: Path) -> None:
    """``prompts/**`` is a capability surface the catalog carves out of its
    docs-only rule, so it claims a project like any other source file."""

    (monorepo / "pyproject.toml").write_text(
        "[project]\nname = 'samples'\n", encoding="utf-8"
    )

    assert (
        resolve_change_scope(
            root=monorepo,
            changed_files=[
                "prompts/system.md",
                "python/agents/crypto-payroll-agent/app/agent.py",
            ],
        )
        is None
    )


def test_scope_declines_without_a_marker_below_the_root(tmp_path: Path) -> None:
    """A sub-directory is not a project just because a diff touches it."""

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'one'\n", encoding="utf-8")
    (tmp_path / "src" / "agent").mkdir(parents=True)
    (tmp_path / "src" / "agent" / "main.py").write_text("x = 1\n", encoding="utf-8")

    assert (
        resolve_change_scope(root=tmp_path, changed_files=["src/agent/main.py"]) is None
    )


def test_scope_climbs_to_a_surviving_directory(monorepo: Path) -> None:
    """A diff that only deletes files names directories the head tree no
    longer has; their surviving parent still carries the marker."""

    scope = resolve_change_scope(
        root=monorepo,
        changed_files=["python/agents/crypto-payroll-agent/deleted/old_agent.py"],
    )

    assert scope is not None
    assert scope.relative == "python/agents/crypto-payroll-agent"


def test_scope_refuses_paths_that_could_leave_the_repository(monorepo: Path) -> None:
    for changed in (["../elsewhere/agent.py"], ["/etc/agent.py"]):
        assert resolve_change_scope(root=monorepo, changed_files=changed) is None


def test_scope_never_leaves_the_workspace_the_caller_named(monorepo: Path) -> None:
    """Preview scoped to one project must not answer with its sibling."""

    assert (
        resolve_change_scope(
            root=monorepo,
            changed_files=["python/agents/RAG/app/agent.py"],
            limit=monorepo / "python/agents/crypto-payroll-agent",
        )
        is None
    )


# --- verify --preview -------------------------------------------------------


def test_preview_routes_init_to_the_changed_project(monorepo: Path) -> None:
    _touch_one_project(monorepo)

    payload = _preview(monorepo)

    action = payload["control"]["next_action"]
    assert action["kind"] == "initialize"
    assert action["command"].endswith(
        f"init --workspace {monorepo / 'python/agents/crypto-payroll-agent'} "
        "--write --json"
    )
    assert "python/agents/crypto-payroll-agent" in action["why"]
    assert payload["control"]["allowed_next_commands"] == [action["command"]]


def test_preview_routes_verify_to_a_project_that_is_already_adopted(
    monorepo: Path,
) -> None:
    """Following the suggestion once must not leave the second preview
    telling the caller to init a directory that now has a manifest."""

    project = monorepo / "python/agents/crypto-payroll-agent"
    init = runner.invoke(app, ["init", "--workspace", str(project), "--write"])
    assert init.exit_code == 0, init.output
    _touch_one_project(monorepo)

    payload = _preview(monorepo)

    action = payload["control"]["next_action"]
    assert action["kind"] == "verify"
    assert f"--workspace {project} --config shipgate.yaml" in action["command"]
    assert "python/agents/crypto-payroll-agent" in payload["headline"]


def test_preview_keeps_the_workspace_root_for_a_single_project_repo(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="solo-agent"), encoding="utf-8"
    )
    (repo / "app").mkdir()
    (repo / "app" / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="solo_agent", tool="lookup"), encoding="utf-8"
    )
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (repo / "app" / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="solo_agent", tool="refund"), encoding="utf-8"
    )
    _commit_all(repo, "change")

    payload = _preview(repo)

    action = payload["control"]["next_action"]
    assert action["kind"] == "initialize"
    assert action["command"].endswith(f"init --workspace {repo} --write --json")


# --- detect -----------------------------------------------------------------


def test_detect_reports_the_ambiguous_scope_and_its_candidates(monorepo: Path) -> None:
    result = runner.invoke(app, ["detect", "--workspace", str(monorepo), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["agent_scope"] == "ambiguous"
    assert [candidate["path"] for candidate in payload["agent_project_candidates"]] == [
        "python/agents/RAG",
        "python/agents/crypto-payroll-agent",
    ]
    assert payload["agent_project_candidates"][0]["agent_names"] == ["ask_rag_agent"]


def test_detect_reports_a_single_scope_for_the_adk_sample() -> None:
    """The single-agent workspace keeps its existing answer."""

    result = detect_workspace(SAMPLES / "google_adk_agent")

    assert result.agent_scope == "single"
    assert [candidate.path for candidate in result.agent_project_candidates] == ["."]
    assert result.next_action.startswith("agents-shipgate init")


def test_a_project_without_a_name_literal_is_still_a_candidate(
    monorepo: Path,
) -> None:
    """The reported PR builds its agent as ``LlmAgent(name=CONFIG.agent_name)``.
    A name-literal-only rule would leave the very project under review out of
    the list of directories the refusal offers."""

    project = monorepo / "python/agents/config-driven"
    (project / "app").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        _PYPROJECT.format(name="config-driven"), encoding="utf-8"
    )
    (project / "app" / "agent.py").write_text(
        "from google.adk.agents import LlmAgent\n"
        "from .config import CONFIG\n\n"
        "root_agent = LlmAgent(name=CONFIG.agent_name, tools=[])\n",
        encoding="utf-8",
    )

    result = detect_workspace(monorepo)

    entry = next(
        candidate
        for candidate in result.agent_project_candidates
        if candidate.path == "python/agents/config-driven"
    )
    assert entry.agent_names == []
    assert entry.marker == "pyproject.toml"


def test_the_refused_workspace_is_never_offered_back_as_a_command(
    monorepo: Path,
) -> None:
    """Agent files that belong to no sub-project make the root a candidate.
    Offering `--workspace <root>` as the recovery would return the caller to
    the command that was just refused."""

    (monorepo / "loose_agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="loose_agent", tool="poke"), encoding="utf-8"
    )
    (monorepo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="samples"), encoding="utf-8"
    )

    result = runner.invoke(
        app, ["init", "--workspace", str(monorepo), "--write", "--json"]
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    candidates = payload["auto_detected"]["agent_project_candidates"]
    assert "." in [candidate["path"] for candidate in candidates]
    commands = [
        action["command"]
        for action in payload["next_actions"]
        if action["kind"] == "command"
    ]
    assert commands
    assert not any(f"--workspace {monorepo} " in command for command in commands)


def test_two_agents_in_one_project_are_one_scope(tmp_path: Path) -> None:
    """A crew, a router and its sub-agents: one project, one manifest."""

    (tmp_path / "pyproject.toml").write_text(
        _PYPROJECT.format(name="crew"), encoding="utf-8"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "researcher.py").write_text(
        _ADK_AGENT_MODULE.format(name="researcher", tool="search"), encoding="utf-8"
    )
    (tmp_path / "app" / "writer.py").write_text(
        _ADK_AGENT_MODULE.format(name="writer", tool="publish"), encoding="utf-8"
    )

    result = detect_workspace(tmp_path)

    assert result.agent_scope == "single"
    assert [candidate.agent_names for candidate in result.agent_project_candidates] == [
        ["researcher", "writer"]
    ]


# --- init -------------------------------------------------------------------


def test_init_refuses_to_write_an_ambiguous_workspace(monorepo: Path) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(monorepo),
            "--write",
            "--ci",
            "--agent-instructions=default",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "refused_ambiguous_scope"
    assert payload["created"] is False
    assert payload["auto_detected"]["agent_scope"] == "ambiguous"
    assert [
        candidate["path"] for candidate in payload["auto_detected"]["agent_project_candidates"]
    ] == ["python/agents/RAG", "python/agents/crypto-payroll-agent"]
    # Rank 1 is the decision, not one of the candidates: promoting a
    # candidate would make the arbitrary pick this refusal prevents.
    assert payload["next_actions"][0]["kind"] == "review"
    assert [action["kind"] for action in payload["next_actions"][1:]] == [
        "command",
        "command",
    ]
    assert str(monorepo / "python/agents/RAG") in payload["next_actions"][1]["command"]


def test_a_refused_init_writes_nothing_at_all(monorepo: Path) -> None:
    """Including the managed .gitignore block: the workspace whose scope
    Shipgate declined to adopt must not carry Shipgate edits (#363)."""

    before = (monorepo / ".gitignore").read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(monorepo),
            "--write",
            "--ci",
            "--claude-code",
            "--agent-instructions=all",
        ],
    )

    assert result.exit_code == 2, result.output
    assert not (monorepo / "shipgate.yaml").exists()
    assert not (monorepo / "AGENTS.md").exists()
    assert not (monorepo / ".github").exists()
    assert not (monorepo / ".claude").exists()
    assert (monorepo / ".gitignore").read_text(encoding="utf-8") == before
    assert _worktree_is_clean(monorepo)


def test_init_writes_when_the_scope_is_named_explicitly(monorepo: Path) -> None:
    project = monorepo / "python/agents/crypto-payroll-agent"
    root_gitignore = (monorepo / ".gitignore").read_text(encoding="utf-8")

    result = runner.invoke(
        app, ["init", "--workspace", str(project), "--write", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "written"
    assert payload["auto_detected"]["agent_name"] == "crypto_payroll_agent"
    assert payload["auto_detected"]["agent_scope"] == "single"
    # The reports directory is ignored where the manifest lives, and the
    # repository's own tracked .gitignore is left alone.
    assert "agents-shipgate-reports/" in (project / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert (monorepo / ".gitignore").read_text(encoding="utf-8") == root_gitignore


def test_init_writes_one_manifest_when_the_scope_is_accepted(monorepo: Path) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(monorepo),
            "--write",
            "--allow-ambiguous-scope",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "written"
    assert (monorepo / "shipgate.yaml").is_file()


def test_minimal_init_is_not_scope_gated(monorepo: Path) -> None:
    """``--minimal`` adopts no detected name or tool surface, so it cannot
    be silently mis-scoped and has nothing to refuse."""

    result = runner.invoke(
        app, ["init", "--workspace", str(monorepo), "--write", "--minimal"]
    )

    assert result.exit_code == 0, result.output
    assert (monorepo / "shipgate.yaml").is_file()


def test_instruction_refresh_still_works_once_a_manifest_exists(monorepo: Path) -> None:
    """An adopted repository has settled its scope; refreshing the agent
    kit must not be blocked by a question that was already answered."""

    written = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(monorepo),
            "--write",
            "--allow-ambiguous-scope",
        ],
    )
    assert written.exit_code == 0, written.output

    refreshed = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(monorepo),
            "--write",
            "--agent-instructions=agents-md",
            "--json",
        ],
    )

    assert refreshed.exit_code == 0, refreshed.output
    payload = json.loads(refreshed.output)
    assert payload["manifest_status"] == "skipped_existing"
    assert (monorepo / "AGENTS.md").is_file()


def test_init_dry_run_reports_the_ambiguity_without_refusing(monorepo: Path) -> None:
    """Without ``--write`` nothing can be mis-scoped on disk, so the draft
    still renders — carrying the scope verdict that routes the caller."""

    result = runner.invoke(app, ["init", "--workspace", str(monorepo), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["auto_detected"]["agent_scope"] == "ambiguous"
    assert payload["template"]
    assert not (monorepo / "shipgate.yaml").exists()


def test_bootstrap_stops_at_init_instead_of_scanning_the_wrong_scope(
    monorepo: Path,
) -> None:
    """The one-command adoption flow inherits the refusal, with the
    candidate list intact in the step payload it stopped on."""

    from agents_shipgate.cli.bootstrap import bootstrap_run

    result = bootstrap_run(workspace=monorepo, ci=False, apply=False, confidence="high")

    assert result["stopped"] is True
    assert result["verdict"] == "failed_at_init"
    assert [step["label"] for step in result["steps"]] == ["detect", "init"]
    payload = result["steps"][-1]["payload"]
    assert payload["manifest_status"] == "refused_ambiguous_scope"
    assert payload["auto_detected"]["agent_project_candidates"]
    assert not (monorepo / "shipgate.yaml").exists()


def _worktree_is_clean(repo: Path) -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return not status.stdout.strip()
