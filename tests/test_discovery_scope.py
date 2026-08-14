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
from agents_shipgate.cli.discovery.scope import (
    manifest_opt_in,
    resolve_change_scope,
)
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


def _preview(
    repo: Path, *, workspace: Path | None = None, refs: bool = True
) -> dict:
    """Run the preview. ``refs=False`` is the *promoted* adoption command —
    plain ``verify --preview --json``, with no ``--base`` and no ``--head``."""

    argv = ["verify", "--workspace", str(workspace or repo), "--preview"]
    if refs:
        argv += ["--base", "origin/main", "--head", "HEAD"]
    argv += ["--format", "json"]
    result = runner.invoke(app, argv)
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
    ).scope

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
        ).scope
        is None
    )


def test_scope_declines_for_a_root_level_change(monorepo: Path) -> None:
    assert resolve_change_scope(root=monorepo, changed_files=["README.md"]).scope is None


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
    ).scope

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
        ).scope
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
        ).scope
        is None
    )


def test_scope_declines_without_a_marker_below_the_root(tmp_path: Path) -> None:
    """A sub-directory is not a project just because a diff touches it."""

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'one'\n", encoding="utf-8")
    (tmp_path / "src" / "agent").mkdir(parents=True)
    (tmp_path / "src" / "agent" / "main.py").write_text("x = 1\n", encoding="utf-8")

    assert (
        resolve_change_scope(root=tmp_path, changed_files=["src/agent/main.py"]).scope
        is None
    )


def test_scope_climbs_to_a_surviving_directory(monorepo: Path) -> None:
    """A diff that only deletes files names directories the head tree no
    longer has; their surviving parent still carries the marker."""

    scope = resolve_change_scope(
        root=monorepo,
        changed_files=["python/agents/crypto-payroll-agent/deleted/old_agent.py"],
    ).scope

    assert scope is not None
    assert scope.relative == "python/agents/crypto-payroll-agent"


def test_scope_refuses_paths_that_could_leave_the_repository(monorepo: Path) -> None:
    for changed in (["../elsewhere/agent.py"], ["/etc/agent.py"]):
        assert resolve_change_scope(root=monorepo, changed_files=changed).scope is None


def test_scope_never_leaves_the_workspace_the_caller_named(monorepo: Path) -> None:
    """Preview scoped to one project must not answer with its sibling."""

    assert (
        resolve_change_scope(
            root=monorepo,
            changed_files=["python/agents/RAG/app/agent.py"],
            limit=monorepo / "python/agents/crypto-payroll-agent",
        ).scope
        is None
    )


def test_scope_declines_when_a_capability_path_belongs_to_no_project(
    monorepo: Path,
) -> None:
    """A root ``prompts/system.md`` with no project above it is not silent —
    it carries a capability surface that no candidate manifest would cover,
    so claiming the change belongs to the sibling project would be false."""

    assert (
        resolve_change_scope(
            root=monorepo,
            changed_files=[
                "prompts/system.md",
                "python/agents/crypto-payroll-agent/app/agent.py",
            ],
        ).scope
        is None
    )


def test_scope_reports_the_projects_a_change_spans(monorepo: Path) -> None:
    """Two projects are not merely "no answer": naming them is what lets
    preview route to discovery instead of to an init that would refuse."""

    resolution = resolve_change_scope(
        root=monorepo,
        changed_files=[
            "python/agents/crypto-payroll-agent/app/agent.py",
            "python/agents/RAG/app/agent.py",
        ],
    )

    assert resolution.scope is None
    assert resolution.contested == (
        "python/agents/RAG",
        "python/agents/crypto-payroll-agent",
    )


def test_scope_does_not_trim_paths(monorepo: Path) -> None:
    """A file name may legally end in a space. Trimming it would attribute
    the change to a directory that does not exist."""

    project = monorepo / "python/agents/crypto-payroll-agent"
    (project / "odd ").mkdir()
    (project / "odd " / "agent.py").write_text("x = 1\n", encoding="utf-8")

    scope = resolve_change_scope(
        root=monorepo,
        changed_files=["python/agents/crypto-payroll-agent/odd /agent.py"],
    ).scope

    assert scope is not None
    assert scope.relative == "python/agents/crypto-payroll-agent"


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


def test_the_promoted_preview_command_scopes_without_explicit_refs(
    monorepo: Path,
) -> None:
    """`verify --preview --json` is the command every adoption flow runs.
    Without base auto-detection it evaluates an empty change set, and the
    scoping below it never fires (#363 review)."""

    _touch_one_project(monorepo)

    payload = _preview(monorepo, refs=False)

    assert payload["base_ref"] == "origin/main"
    assert payload["changed_files"] == [
        "python/agents/crypto-payroll-agent/app/agent.py"
    ]
    action = payload["control"]["next_action"]
    assert action["kind"] == "initialize"
    assert str(monorepo / "python/agents/crypto-payroll-agent") in action["command"]


def test_no_base_keeps_the_preview_diffless(monorepo: Path) -> None:
    """--no-base disables detection here exactly as it does for verify."""

    _touch_one_project(monorepo)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(monorepo),
            "--preview",
            "--no-base",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["base_ref"] is None
    assert payload["changed_files"] == []


def test_preview_routes_to_discovery_when_the_change_spans_projects(
    monorepo: Path,
) -> None:
    """Recommending root init here would recommend a command that refuses."""

    for project, tool in (
        ("crypto-payroll-agent", "refund"),
        ("RAG", "reindex"),
    ):
        target = monorepo / "python/agents" / project / "app/agent.py"
        target.write_text(
            target.read_text(encoding="utf-8")
            + f'\n\ndef {tool}(target: str) -> str:\n    """Do."""\n    return "ok"\n',
            encoding="utf-8",
        )
    _commit_all(monorepo, "touch both projects")

    payload = _preview(monorepo)

    action = payload["control"]["next_action"]
    assert action["kind"] == "discover"
    assert action["command"].endswith(f"detect --workspace {monorepo} --json")
    assert "python/agents/RAG" in action["why"]
    assert "python/agents/crypto-payroll-agent" in action["why"]


def test_preview_claims_no_scope_for_a_head_that_is_not_checked_out(
    monorepo: Path,
) -> None:
    """Markers are read from the working tree, so evaluating another ref
    establishes nothing — and "nothing established" must not become a
    manifest for whichever agent the current checkout happens to hold."""

    _touch_one_project(monorepo)
    subprocess.run(["git", "branch", "feature"], cwd=monorepo, check=True)
    subprocess.run(["git", "checkout", "-q", "origin/main"], cwd=monorepo, check=True)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(monorepo),
            "--preview",
            "--base",
            "origin/main",
            "--head",
            "feature",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    action = json.loads(result.output)["control"]["next_action"]
    assert action["kind"] == "discover"
    assert "not the commit this worktree has checked out" in action["why"]
    assert "init --workspace" not in action["command"]


def test_preview_prefers_the_changed_project_manifest_over_the_root_one(
    monorepo: Path,
) -> None:
    """A root manifest governs a different boundary than the nested project
    the diff actually changed."""

    project = monorepo / "python/agents/crypto-payroll-agent"
    for workspace in (monorepo, project):
        init = runner.invoke(
            app,
            [
                "init",
                "--workspace",
                str(workspace),
                "--write",
                "--allow-unresolved-scope",
            ],
        )
        assert init.exit_code == 0, init.output
    # Adoption lands before the pull request under review, so the diff is
    # the agent change alone rather than the manifests that gate it.
    _commit_all(monorepo, "adopt at both levels")
    _set_origin_main(monorepo)
    _touch_one_project(monorepo)

    payload = _preview(monorepo)

    action = payload["control"]["next_action"]
    assert action["kind"] == "verify"
    assert f"--workspace {project} --config shipgate.yaml" in action["command"]


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


def test_preview_sees_uncommitted_work(monorepo: Path) -> None:
    """The command preview emits runs against the worktree, so preview has to
    scope from the worktree — an uncommitted-only capability change read as an
    empty diff and authorized root init (#363 review)."""

    target = monorepo / "python/agents/crypto-payroll-agent/app/agent.py"
    target.write_text(
        target.read_text(encoding="utf-8")
        + '\n\ndef refund(target: str) -> str:\n    """Refund."""\n    return "ok"\n',
        encoding="utf-8",
    )

    payload = _preview(monorepo, refs=False)

    assert payload["changed_files"] == [
        "python/agents/crypto-payroll-agent/app/agent.py"
    ]
    action = payload["control"]["next_action"]
    assert action["kind"] == "initialize"
    assert str(monorepo / "python/agents/crypto-payroll-agent") in action["command"]


def test_preview_sees_untracked_work(monorepo: Path) -> None:
    """A brand-new project directory is untracked, which is exactly the shape
    of a first adoption."""

    project = _write_agent_project(
        monorepo, "python/agents/new-agent", name="new_agent", tool="ship"
    )

    payload = _preview(monorepo, refs=False)

    assert "python/agents/new-agent/app/agent.py" in payload["changed_files"]
    action = payload["control"]["next_action"]
    assert action["kind"] == "initialize"
    assert str(project) in action["command"]


def test_preview_routes_contested_configured_projects_to_review(
    monorepo: Path,
) -> None:
    """Two configured projects are two governance boundaries; a root manifest
    is not a substitute for either (#363 review)."""

    for name in ("crypto-payroll-agent", "RAG"):
        init = runner.invoke(
            app,
            ["init", "--workspace", str(monorepo / "python/agents" / name), "--write"],
        )
        assert init.exit_code == 0, init.output
    root_init = runner.invoke(
        app,
        ["init", "--workspace", str(monorepo), "--write", "--allow-unresolved-scope"],
    )
    assert root_init.exit_code == 0, root_init.output
    _commit_all(monorepo, "adopt everywhere")
    _set_origin_main(monorepo)
    for name, tool in (("crypto-payroll-agent", "refund"), ("RAG", "reindex")):
        target = monorepo / "python/agents" / name / "app/agent.py"
        target.write_text(
            target.read_text(encoding="utf-8")
            + f'\n\ndef {tool}(x: str) -> str:\n    """Do."""\n    return "ok"\n',
            encoding="utf-8",
        )
    _commit_all(monorepo, "touch both")

    payload = _preview(monorepo)

    action = payload["control"]["next_action"]
    assert action["actor"] == "human"
    assert action["kind"] == "review"
    # One command cannot honor two gates, so the boundaries are named in the
    # route a human reads rather than authorized as a pick among them.
    assert payload["control"]["allowed_next_commands"] == []
    for name in ("crypto-payroll-agent", "RAG"):
        assert f"python/agents/{name} --config" in action["why"]


def test_preview_ignores_a_symlinked_manifest(monorepo: Path) -> None:
    """The verifier refuses a manifest path with symlink components, so
    routing to one would promise a command that exits 2."""

    project = monorepo / "python/agents/crypto-payroll-agent"
    external = monorepo.parent / "external-shipgate.yaml"
    external.write_text("schema_version: 0.1\n", encoding="utf-8")
    (project / "shipgate.yaml").symlink_to(external)
    _touch_one_project(monorepo)

    payload = _preview(monorepo)

    action = payload["control"]["next_action"]
    assert action["kind"] != "verify"


def test_scoped_reports_land_beside_the_manifest_that_ignores_them(
    monorepo: Path,
) -> None:
    """init installs the managed ignore in the project; the verifier must
    write the reports it covers there too (#363 review)."""

    project = monorepo / "python/agents/crypto-payroll-agent"
    init = runner.invoke(app, ["init", "--workspace", str(project), "--write"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(project),
            "--config",
            "shipgate.yaml",
            "--ci-mode",
            "advisory",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (project / "agents-shipgate-reports").is_dir()
    assert not (monorepo / "agents-shipgate-reports").exists()
    assert "agents-shipgate-reports/" in (project / ".gitignore").read_text("utf-8")


def test_an_explicit_out_still_resolves_against_the_repository_root(
    monorepo: Path,
) -> None:
    """Only the default moved. A caller that names a directory keeps writing
    exactly where it wrote before."""

    project = monorepo / "python/agents/crypto-payroll-agent"
    init = runner.invoke(app, ["init", "--workspace", str(project), "--write"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(project),
            "--config",
            "shipgate.yaml",
            "--out",
            "shared-reports",
            "--ci-mode",
            "advisory",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (monorepo / "shared-reports").is_dir()


def test_the_command_init_emits_finds_the_manifest_it_wrote(
    monorepo: Path,
) -> None:
    """A scoped manifest needs a qualified path in the emitted next action.

    `-c shipgate.yaml` resolves against the working directory, so a manifest
    written into `apps/a` is not found from the repository root and the emitted
    command exits 2.

    Contract v24 changed *which* command carries that property, not whether it
    holds. `init --write` always leaves an unresolved `agent.declared_purpose`,
    which is a declaration a person makes, so its rank-1 route is now a human
    review with no command (#325). The qualified path is asserted here on the
    route an adopter actually reaches next — `doctor`, once the declaration is
    supplied — and separately on the composer itself.
    """

    project = monorepo / "python/agents/crypto-payroll-agent"

    result = runner.invoke(
        app, ["init", "--workspace", str(project), "--write", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    manifest = project / "shipgate.yaml"
    assert manifest.is_file()
    assert payload["control"]["control_state"] == "human_review_required"
    assert payload["next_actions"][0]["command"] is None

    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("- CHANGE_ME", "- Run payroll"),
        encoding="utf-8",
    )
    doctor = runner.invoke(app, ["doctor", "--config", str(manifest), "--json"])
    command = json.loads(doctor.output)[0]["next_actions"][0]["command"]

    assert str(manifest) in command
    assert str(project) in command


def test_the_scan_command_init_composes_names_the_manifest_it_wrote() -> None:
    """The composer main added, covered directly.

    It feeds `init`'s onward route, which is selected whenever the manifest it
    wrote owes nobody a declaration. Today's template always leaves one, so the
    CLI test above reaches the property through `doctor` instead; this keeps the
    helper itself pinned rather than relying on a path that is currently
    unreachable end to end.
    """

    from agents_shipgate.cli._register_init import _scan_command_config

    root = Path.cwd().resolve()
    assert _scan_command_config(root / "shipgate.yaml") == "shipgate.yaml"
    scoped = root / "apps" / "a" / "shipgate.yaml"
    assert _scan_command_config(scoped) == str(scoped)


def test_a_relative_adoption_kit_is_not_copied_into_a_candidate(
    monorepo: Path,
) -> None:
    """A kit path resolves under the workspace, so copying a root-relative one
    into a command that runs elsewhere emits a command that exits 2."""

    kit_dir = monorepo / ".agents-shipgate"
    kit_dir.mkdir(exist_ok=True)
    (kit_dir / "kit.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(monorepo),
            "--write",
            "--agent-instructions=agents-md",
            "--agent-instructions-kit",
            ".agents-shipgate/kit.yaml",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    commands = [
        action["command"]
        for action in payload["next_actions"]
        if action["kind"] == "command"
    ]
    assert not any(".agents-shipgate/kit.yaml" in command for command in commands)
    assert any(
        action["kind"] == "review" and "adoption kit" in action["why"]
        for action in payload["next_actions"][1:]
    )


def test_requirements_only_siblings_are_two_projects(tmp_path: Path) -> None:
    """A bare requirements.txt is not a project boundary; one beside an agent
    is the only boundary that layout has (#363 review)."""

    repo = tmp_path / "reqs"
    for name in ("one", "two"):
        project = repo / f"agent_{name}"
        project.mkdir(parents=True)
        (project / "requirements.txt").write_text("google-adk\n", encoding="utf-8")
        (project / "agent.py").write_text(
            _ADK_AGENT_MODULE.format(name=f"agent_{name}", tool="act"),
            encoding="utf-8",
        )

    result = detect_workspace(repo)

    assert result.agent_scope == "ambiguous"
    assert [candidate.path for candidate in result.agent_project_candidates] == [
        "agent_one",
        "agent_two",
    ]
    assert {c.marker for c in result.agent_project_candidates} == {"requirements.txt"}


def test_a_bare_requirements_file_is_not_a_project_boundary(tmp_path: Path) -> None:
    """The weak marker needs evidence in the same directory, or every
    `tests/requirements.txt` would become a project root."""

    repo = tmp_path / "solo"
    (repo / "tests").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="solo"), encoding="utf-8"
    )
    (repo / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="solo_agent", tool="act"), encoding="utf-8"
    )
    (repo / "tests" / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    result = detect_workspace(repo)

    assert result.agent_scope == "single"
    assert [candidate.path for candidate in result.agent_project_candidates] == ["."]


def test_an_unrelated_agent_class_does_not_define_a_project(tmp_path: Path) -> None:
    """A module with no framework import that constructs its own `Agent` is
    not an agent project, and reading it as one refuses `init` on a repository
    that has exactly one (#363 review)."""

    repo = tmp_path / "mixed"
    (repo / "adk").mkdir(parents=True)
    (repo / "adk" / "pyproject.toml").write_text(
        _PYPROJECT.format(name="adk"), encoding="utf-8"
    )
    (repo / "adk" / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="real_agent", tool="act"), encoding="utf-8"
    )
    (repo / "crm").mkdir()
    (repo / "crm" / "pyproject.toml").write_text(
        _PYPROJECT.format(name="crm"), encoding="utf-8"
    )
    (repo / "crm" / "models.py").write_text(
        "class Agent:\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n\n\n"
        'sales = Agent(name="crm_rep")\n',
        encoding="utf-8",
    )

    result = detect_workspace(repo)

    assert result.agent_scope == "single"
    assert [candidate.path for candidate in result.agent_project_candidates] == ["adk"]
    # Nor is it a name suggestion any more. `Agent` here is bound to a local
    # `class` in the same file, so it is not a framework constructor and
    # `crm_rep` is not an agent name — offering it under
    # `source: Agent_name_literal` claimed a provenance it never had, and
    # `init` could write it as the reviewed identity (#371 review round 3).
    assert "crm_rep" not in [c.value for c in result.agent_name_candidates]
    assert "real_agent" in [c.value for c in result.agent_name_candidates]


def test_a_nested_manifest_opts_the_change_in(tmp_path: Path) -> None:
    """The documented nested-manifest opt-in has to hold outside preview too:
    `trigger` computed it from the workspace root alone (#363 review)."""

    repo = tmp_path / "apps-repo"
    project = repo / "apps" / "a"
    project.mkdir(parents=True)
    (project / "shipgate.yaml").write_text("schema_version: 0.1\n", encoding="utf-8")

    assert manifest_opt_in(repo, changed_paths=["apps/a/README.md"]) is True
    # An unrelated change is not opted in by somebody else's adoption.
    assert manifest_opt_in(repo, changed_paths=["docs/guide.md"]) is False


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
    assert payload["manifest_status"] == "refused_unresolved_scope"
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
            "--allow-unresolved-scope",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "written"
    assert (monorepo / "shipgate.yaml").is_file()


def test_scoped_ci_lands_at_the_repository_root(monorepo: Path) -> None:
    """GitHub loads workflows from the repository root and nowhere else, so
    a workflow beside a nested manifest is a gate that never runs — while
    init reports it as written."""

    project = monorepo / "python/agents/crypto-payroll-agent"

    result = runner.invoke(
        app, ["init", "--workspace", str(project), "--write", "--ci", "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workflow"]["status"] == "written"
    workflow = Path(payload["workflow"]["path"])
    assert workflow.is_file()
    assert workflow.parent == monorepo / ".github/workflows"
    assert not (project / ".github").exists()
    # The action runs at the repository root, so the manifest it gates has
    # to be named from there.
    assert (
        "config: python/agents/crypto-payroll-agent/shipgate.yaml"
        in workflow.read_text(encoding="utf-8")
    )


def test_every_scoped_project_gets_its_own_gate(monorepo: Path) -> None:
    """The action takes one `config` scalar, so a shared workflow gates
    whichever project ran first and leaves the rest ungated while `--ci`
    reports a skip (#363 review)."""

    written: dict[str, str] = {}
    for name in ("crypto-payroll-agent", "RAG"):
        result = runner.invoke(
            app,
            [
                "init",
                "--workspace",
                str(monorepo / "python/agents" / name),
                "--write",
                "--ci",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["workflow"]["status"] == "written", name
        written[name] = payload["workflow"]["path"]

    assert written["crypto-payroll-agent"] != written["RAG"]
    for name, path in written.items():
        assert f"python/agents/{name}/shipgate.yaml" in Path(path).read_text("utf-8")


def test_a_second_run_still_refuses_to_double_wire_one_project(
    monorepo: Path,
) -> None:
    """Per-project workflows must not weaken the double-wiring guard for the
    project they gate."""

    project = monorepo / "python/agents/crypto-payroll-agent"
    first = runner.invoke(
        app, ["init", "--workspace", str(project), "--write", "--ci", "--json"]
    )
    assert first.exit_code == 0, first.output
    (project / "shipgate.yaml").unlink()

    second = runner.invoke(
        app, ["init", "--workspace", str(project), "--write", "--ci", "--json"]
    )

    assert second.exit_code == 0, second.output
    status = json.loads(second.output)["workflow"]["status"]
    assert status in ("skipped_existing_target", "skipped_cross_reference")


def test_root_scoped_ci_is_unchanged(tmp_path: Path) -> None:
    """The ordinary root adoption keeps writing the plain config path."""

    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="solo"), encoding="utf-8"
    )
    (repo / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="solo_agent", tool="lookup"), encoding="utf-8"
    )

    result = runner.invoke(app, ["init", "--workspace", str(repo), "--write", "--ci"])

    assert result.exit_code == 0, result.output
    workflow = (repo / ".github/workflows/agents-shipgate.yml").read_text("utf-8")
    assert "config: shipgate.yaml" in workflow


def test_the_refusal_repeats_the_setup_flags_it_was_given(monorepo: Path) -> None:
    """A recovery command that drops --ci completes with less than the
    caller asked for, and reports success for it."""

    result = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(monorepo),
            "--write",
            "--ci",
            "--agent-instructions=agents-md",
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    commands = [
        action["command"]
        for action in payload["next_actions"]
        if action["kind"] == "command"
    ]
    assert commands
    for command in commands:
        assert "--ci" in command
        assert "--agent-instructions=agents-md" in command


def test_capped_discovery_refuses_instead_of_claiming_one_project(
    tmp_path: Path,
) -> None:
    """Projects `a` and `z` separated by more filler than the parse cap: the
    verdict must not depend on which files the walk reached first."""

    repo = tmp_path / "capped"
    for name in ("a", "z"):
        project = repo / name
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            _PYPROJECT.format(name=name), encoding="utf-8"
        )
        (project / "agent.py").write_text(
            _ADK_AGENT_MODULE.format(name=f"agent_{name}", tool="act"),
            encoding="utf-8",
        )
    filler = repo / "filler"
    filler.mkdir()
    for index in range(1200):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")

    capped = detect_workspace(repo)
    assert capped.agent_scope == "unknown"
    assert detect_workspace(repo, max_python_files=5000).agent_scope == "ambiguous"

    result = runner.invoke(app, ["init", "--workspace", str(repo), "--write", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "refused_unresolved_scope"
    assert payload["auto_detected"]["agent_scope"] == "unknown"
    assert not (repo / "shipgate.yaml").exists()


def test_a_large_single_project_repo_keeps_its_single_verdict(
    tmp_path: Path,
) -> None:
    """Truncation alone cannot make a workspace unresolved: with one project
    root there is nowhere for a second project to hide."""

    repo = tmp_path / "solo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="solo"), encoding="utf-8"
    )
    (repo / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="solo_agent", tool="act"), encoding="utf-8"
    )
    for index in range(1200):
        (repo / "pkg" / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")

    assert detect_workspace(repo).agent_scope == "single"


def test_artifact_only_projects_are_ambiguous_without_any_python(
    tmp_path: Path,
) -> None:
    """Two OpenAPI package projects have no framework detection and no name
    literal, but one root manifest would still declare both."""

    repo = tmp_path / "services"
    for name in ("a", "b"):
        project = repo / "packages" / name
        project.mkdir(parents=True)
        (project / "package.json").write_text(
            json.dumps({"name": f"svc-{name}", "version": "1.0.0"}), encoding="utf-8"
        )
        (project / "openapi.yaml").write_text(
            "openapi: 3.0.0\n"
            f"info: {{title: svc-{name}, version: 1.0.0}}\n"
            "paths:\n"
            f"  /pay-{name}:\n"
            "    post:\n"
            f"      operationId: pay_{name}\n"
            "      summary: Send money.\n"
            '      responses: {"200": {description: ok}}\n',
            encoding="utf-8",
        )

    result = detect_workspace(repo)

    assert result.is_agent_project is False
    assert result.agent_scope == "ambiguous"
    assert [candidate.path for candidate in result.agent_project_candidates] == [
        "packages/a",
        "packages/b",
    ]

    refused = runner.invoke(
        app, ["init", "--workspace", str(repo), "--write", "--json"]
    )
    assert refused.exit_code == 2, refused.output
    assert not (repo / "shipgate.yaml").exists()


def test_nested_manifests_make_a_workspace_ambiguous(tmp_path: Path) -> None:
    """A `shipgate.yaml` in a sub-directory is a scope somebody already drew
    by hand; two of them settle the question without any heuristic."""

    repo = tmp_path / "workspace"
    for name in ("billing", "support"):
        project = repo / name
        project.mkdir(parents=True)
        (project / "shipgate.yaml").write_text(
            "schema_version: 0.1\n", encoding="utf-8"
        )

    result = detect_workspace(repo)

    assert result.agent_scope == "ambiguous"
    assert [candidate.path for candidate in result.agent_project_candidates] == [
        "billing",
        "support",
    ]


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
            "--allow-unresolved-scope",
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
    assert payload["manifest_status"] == "refused_unresolved_scope"
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
