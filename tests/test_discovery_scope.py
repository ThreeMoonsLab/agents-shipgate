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
from agents_shipgate.cli.discovery.signals import weak_marker_evidence_dirs
from agents_shipgate.cli.main import app
from agents_shipgate.invocation import render_command

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


def _init_command(target: Path) -> str:
    """The scoped ``init`` exactly as this process would spell it (#322)."""

    return render_command(["init", "--workspace", str(target), "--write", "--json"])


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


def _preview_other_head(repo: Path) -> dict:
    argv = [
        "verify",
        "--workspace",
        str(repo),
        "--preview",
        "--base",
        "origin/main",
        "--head",
        "feature",
        "--format",
        "json",
    ]
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_preview_claims_no_scope_for_a_head_that_is_not_checked_out(
    monorepo: Path,
) -> None:
    """Markers are read from the working tree, so evaluating another ref
    establishes nothing — and "nothing established" must not become a
    manifest for whichever agent the current checkout happens to hold.

    Nor a *command* about whichever agent it holds: discovery of the current
    worktree answers a question about a different tree, and its single-scope
    answer routes straight back to a root `init` for the unrelated project
    (#399 review).

    What is missing is an input, not a judgement — a working tree holding the
    commit under review — so the route names it and stays with the coding
    agent. Handing this to a human published `must_stop` and `command: null`
    for a state one checkout clears (#397)."""

    _touch_one_project(monorepo)
    subprocess.run(["git", "branch", "feature"], cwd=monorepo, check=True)
    subprocess.run(["git", "checkout", "-q", "origin/main"], cwd=monorepo, check=True)

    payload = _preview_other_head(monorepo)

    control = payload["control"]
    action = control["next_action"]
    assert control["state"] == "agent_action_required"
    assert control["must_stop"] is False
    assert action["actor"] == "coding_agent"
    assert action["kind"] == "fetch_base"
    assert action["command"] is None
    assert action["expects"] == "feature checked out in this worktree"
    assert "not the commit this worktree has checked out" in action["why"]
    assert "Check feature out in this worktree" in action["why"]
    # A `fetch_base` route carries no command by contract, and none is smuggled
    # in beside it: the step that changes the answer is the checkout.
    assert control["allowed_next_commands"] == []
    assert control["permissions"]["report_complete"] is False


def test_checking_the_head_out_clears_the_preview_that_asked_for_it(
    monorepo: Path,
) -> None:
    """The route is only worth publishing if taking it moves the loop.

    Same command, same refs, one checkout apart: the second run must name the
    project the pull request changed rather than return the same request."""

    _touch_one_project(monorepo)
    subprocess.run(["git", "branch", "feature"], cwd=monorepo, check=True)
    subprocess.run(["git", "checkout", "-q", "origin/main"], cwd=monorepo, check=True)
    assert _preview_other_head(monorepo)["control"]["next_action"]["kind"] == "fetch_base"

    subprocess.run(["git", "checkout", "-q", "feature"], cwd=monorepo, check=True)

    action = _preview_other_head(monorepo)["control"]["next_action"]
    assert action["kind"] == "initialize"
    assert (
        f"--workspace {monorepo / 'python/agents/crypto-payroll-agent'} --write"
        in action["command"]
    )


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


def _write_requirements_only_project(root: Path, relative: str, *, name: str) -> Path:
    """An agent whose entire project boundary is a requirements file.

    The shape `verify --preview` routed to the repository root: no
    `pyproject.toml`, so nothing but the `requirements.txt` beside
    `agent.py` says where the project starts (#394).
    """

    project = root / relative
    project.mkdir(parents=True)
    (project / "requirements.txt").write_text("google-adk\n", encoding="utf-8")
    (project / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name=name, tool="act"), encoding="utf-8"
    )
    return project


@pytest.fixture
def weak_marker_monorepo(tmp_path: Path) -> Path:
    """A monorepo whose changed project is bounded by a requirements file."""

    repo = _init_repo(tmp_path)
    _write_requirements_only_project(
        repo, "python/agents/smart_closer", name="smart_closer"
    )
    _write_agent_project(
        repo, "python/agents/rag", name="ask_rag_agent", tool="ask"
    )
    (repo / "README.md").write_text("# samples\n", encoding="utf-8")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    return repo


def test_evidence_unlocks_the_weak_marker_the_preview_path_walks_past(
    weak_marker_monorepo: Path,
) -> None:
    """The resolver cannot see a requirements-only project on its own, and
    that is the whole gap: without evidence the walk climbs to the root and
    reports `not_narrowed`, which routing spends on a root `init` (#394)."""

    changed = ["python/agents/smart_closer/agent.py"]

    blind = resolve_change_scope(root=weak_marker_monorepo, changed_files=changed)
    assert blind.status == "not_narrowed"
    assert blind.scope is None

    evidence = weak_marker_evidence_dirs(weak_marker_monorepo, changed)
    assert evidence.undetermined == ()
    assert evidence.directories == frozenset(
        {(weak_marker_monorepo / "python/agents/smart_closer").resolve()}
    )

    seeing = resolve_change_scope(
        root=weak_marker_monorepo,
        changed_files=changed,
        evidence_dirs=evidence.directories,
    )
    assert seeing.status == "resolved"
    assert seeing.scope is not None
    assert seeing.scope.relative == "python/agents/smart_closer"
    assert seeing.scope.marker == "requirements.txt"


def test_preview_routes_init_to_a_requirements_only_project(
    weak_marker_monorepo: Path,
) -> None:
    """The reproduction: every changed path under one requirements-only
    project, and the command preview emits must be one `init` can run."""

    target = weak_marker_monorepo / "python/agents/smart_closer/agent.py"
    target.write_text(
        target.read_text(encoding="utf-8")
        + '\n\ndef followup(deal: str) -> str:\n    """Follow up."""\n'
        '    return "sent"\n',
        encoding="utf-8",
    )
    _commit_all(weak_marker_monorepo, "add followup tool")

    payload = _preview(weak_marker_monorepo)

    action = payload["control"]["next_action"]
    assert action["kind"] == "initialize"
    assert action["command"].endswith(
        f"init --workspace {weak_marker_monorepo / 'python/agents/smart_closer'} "
        "--write --json"
    )
    assert "python/agents/smart_closer" in action["why"]

    # The command has to *work*: routing to the root emitted one that refuses
    # deterministically, which is the failure the issue reports.
    written = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(weak_marker_monorepo / "python/agents/smart_closer"),
            "--write",
            "--json",
        ],
    )
    assert written.exit_code == 0, written.output
    assert json.loads(written.output)["manifest_status"] == "written"


def test_two_requirements_only_projects_contest_the_scope(tmp_path: Path) -> None:
    """Seeing the boundary matters in the refusing direction too. While these
    projects were invisible, a change spanning both looked like an unnarrowed
    single-project change and routing spent it on a root `init` (#394)."""

    repo = _init_repo(tmp_path)
    for name in ("alpha", "beta"):
        _write_requirements_only_project(repo, f"agents/{name}", name=f"{name}_agent")

    changed = ["agents/alpha/agent.py", "agents/beta/agent.py"]
    resolution = resolve_change_scope(
        root=repo,
        changed_files=changed,
        evidence_dirs=weak_marker_evidence_dirs(repo, changed).directories,
    )

    assert resolution.status == "contested"
    assert resolution.contested == ("agents/alpha", "agents/beta")


def test_an_artifact_only_project_unlocks_its_weak_marker(tmp_path: Path) -> None:
    """`detect` counts OpenAPI/MCP artifacts and Codex plugin packages as
    agent evidence, so preview has to as well. A Python service exposing a
    spec beside its requirements file is a project both must see, or the
    command preview emits is one `init` refuses (#399 review)."""

    repo = _init_repo(tmp_path)
    api = repo / "services" / "api"
    api.mkdir(parents=True)
    (api / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (api / "openapi.yaml").write_text(
        "openapi: 3.0.0\n"
        "info: {title: api, version: 1.0.0}\n"
        "paths:\n"
        "  /pay:\n"
        "    post:\n"
        "      operationId: pay\n"
        "      summary: Send money.\n"
        '      responses: {"200": {description: ok}}\n',
        encoding="utf-8",
    )
    _write_agent_project(
        repo, "services/worker", name="worker_agent", tool="work"
    )
    _commit_all(repo, "base")

    changed = ["services/api/openapi.yaml"]
    evidence = weak_marker_evidence_dirs(repo, changed)
    assert evidence.undetermined == ()
    assert evidence.directories == frozenset({(repo / "services/api").resolve()})

    # And the two agree about it, which is the property the omission broke.
    detected = {c.path for c in detect_workspace(repo).agent_project_candidates}
    assert detected == {"services/api", "services/worker"}


def test_a_deleted_agent_leaves_its_weak_marker_undetermined(tmp_path: Path) -> None:
    """The probe reads the head tree. When the change under review deletes the
    one file that was the evidence, "no evidence" is not an answer — it would
    route to a workspace-root `init` that adopts an unrelated project's agent
    (#399 review)."""

    repo = _init_repo(tmp_path)
    gone = repo / "services" / "gone"
    gone.mkdir(parents=True)
    (gone / "requirements.txt").write_text("google-adk\n", encoding="utf-8")
    (gone / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="gone_agent", tool="act"), encoding="utf-8"
    )
    _write_agent_project(repo, "services/other", name="other_agent", tool="ask")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    subprocess.run(["git", "rm", "-q", "services/gone/agent.py"], cwd=repo, check=True)
    _commit_all(repo, "remove the gone agent")

    evidence = weak_marker_evidence_dirs(repo, ["services/gone/agent.py"])
    assert evidence.directories == frozenset()
    assert [entry.path for entry in evidence.undetermined] == ["services/gone"]
    assert evidence.causes == {"deleted_evidence"}

    payload = _preview(repo)
    action = payload["control"]["next_action"]
    # And no command, because none would settle it: a head-only `detect` sees
    # only the surviving project, reports it as the workspace's single scope,
    # and its `init` succeeds with an agent this change never touched — the
    # generic discovery route recovering into the wrong answer (#399 review).
    assert action["actor"] == "human"
    assert action["kind"] == "review"
    assert action["command"] is None
    assert "services/gone" in action["why"]
    assert payload["control"]["allowed_next_commands"] == []

    # The route it replaced, run explicitly, is exactly the wrong answer.
    detected = runner.invoke(app, ["detect", "--workspace", str(repo), "--json"])
    assert json.loads(detected.output)["agent_scope"] == "single"


def test_an_ignored_file_is_not_evidence_the_recommended_command_can_see(
    tmp_path: Path,
) -> None:
    """Routing must not depend on local ignore state. `detect` inventories
    through git, so an ignored agent file beside a requirements file is
    invisible to the command preview recommends — and preview narrowing to
    that directory anyway is preview disagreeing with itself (#399 review)."""

    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text("generated_agent.py\n", encoding="utf-8")
    ignored = repo / "services" / "ignored"
    ignored.mkdir(parents=True)
    (ignored / "requirements.txt").write_text("google-adk\n", encoding="utf-8")
    (ignored / "generated_agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="ignored_agent", tool="act"), encoding="utf-8"
    )
    (ignored / "util.py").write_text("x = 1\n", encoding="utf-8")
    _write_agent_project(repo, "services/real", name="real_agent", tool="ask")
    _commit_all(repo, "base")

    evidence = weak_marker_evidence_dirs(repo, ["services/ignored/util.py"])
    assert evidence.directories == frozenset()
    assert evidence.undetermined == ()
    assert [c.path for c in detect_workspace(repo).agent_project_candidates] == [
        "services/real"
    ]


def test_an_artifact_glob_project_unlocks_its_weak_marker(tmp_path: Path) -> None:
    """`_agent_project_candidates` counts every framework's `candidate_files`,
    including the ones the artifact-glob detectors (Anthropic, OpenAI API,
    n8n, Conductor) fire on. Reading only the OpenAPI/MCP suggestion families
    left `requirements.txt` beside an `openai-config.json` invisible to
    preview and visible to detect (#399 review)."""

    repo = _init_repo(tmp_path)
    api = repo / "services" / "api"
    api.mkdir(parents=True)
    (api / "requirements.txt").write_text("openai\n", encoding="utf-8")
    (api / "openai-config.json").write_text('{"model": "gpt-4"}', encoding="utf-8")
    _write_agent_project(repo, "services/worker", name="worker_agent", tool="work")
    _commit_all(repo, "base")

    evidence = weak_marker_evidence_dirs(repo, ["services/api/openai-config.json"])
    assert evidence.undetermined == ()
    assert evidence.directories == frozenset({(repo / "services/api").resolve()})
    assert {c.path for c in detect_workspace(repo).agent_project_candidates} == {
        "services/api",
        "services/worker",
    }


def test_a_deleted_artifact_leaves_its_weak_marker_undetermined(
    tmp_path: Path,
) -> None:
    """Deletion uncertainty has to cover every family that can unlock the
    marker. Limiting it to `.py` left an artifact-only project silently
    negative, and preview then prescribed a root `init` that succeeds and
    adopts the unrelated surviving agent (#399 review)."""

    repo = _init_repo(tmp_path)
    gone = repo / "services" / "gone"
    gone.mkdir(parents=True)
    (gone / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (gone / "openapi.yaml").write_text(
        "openapi: 3.0.0\n"
        "info: {title: gone, version: 1.0.0}\n"
        "paths:\n"
        "  /pay:\n"
        "    post:\n"
        "      operationId: pay\n"
        "      summary: Send money.\n"
        '      responses: {"200": {description: ok}}\n',
        encoding="utf-8",
    )
    _write_agent_project(repo, "services/other", name="other_agent", tool="ask")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    subprocess.run(
        ["git", "rm", "-q", "services/gone/openapi.yaml"], cwd=repo, check=True
    )
    _commit_all(repo, "remove the spec")

    evidence = weak_marker_evidence_dirs(repo, ["services/gone/openapi.yaml"])
    assert evidence.directories == frozenset()
    assert evidence.causes == {"deleted_evidence"}

    action = _preview(repo)["control"]["next_action"]
    assert action["actor"] == "human"
    assert action["command"] is None


def test_evidence_beyond_the_scoped_cap_is_not_evidence(tmp_path: Path) -> None:
    """The probe reads one directory's own files; the command it recommends
    spends the cap over that directory's whole subtree in inventory order. A
    direct `zz_agent.py` sorting after a thousand inert modules was evidence
    preview could see and the scoped `detect` could not (#399 review)."""

    repo = _init_repo(tmp_path)
    api = repo / "services" / "api"
    (api / "aa_filler").mkdir(parents=True)
    (api / "requirements.txt").write_text("google-adk\n", encoding="utf-8")
    for index in range(1001):
        (api / "aa_filler" / f"mod{index:05d}.py").write_text(
            "x = 1\n", encoding="utf-8"
        )
    (api / "zz_agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="api_agent", tool="act"), encoding="utf-8"
    )
    _commit_all(repo, "base")

    evidence = weak_marker_evidence_dirs(repo, ["services/api/zz_agent.py"])
    assert evidence.directories == frozenset()
    assert evidence.causes == {"parse_budget"}

    # Which is what the recommended command would have reported.
    scoped = detect_workspace(api)
    assert scoped.is_agent_project is False
    assert scoped.python_parse_truncated is True

    # At a bound that reaches it, both agree the other way.
    assert (
        weak_marker_evidence_dirs(
            repo, ["services/api/zz_agent.py"], max_python_files=2000
        ).directories
        == frozenset({api.resolve()})
    )


def test_a_deleted_project_boundary_is_not_a_directory_that_was_never_one(
    tmp_path: Path,
) -> None:
    """Every other question here is asked of the head tree, which is the one
    place a removed boundary is guaranteed not to be. Deleting a whole project
    left nothing for the marker filter to find, so preview attributed the
    change to what survived and recommended a root `init` for an unrelated
    agent (#399 review)."""

    repo = _init_repo(tmp_path)
    _write_agent_project(repo, "services/gone", name="gone_agent", tool="go")
    _write_agent_project(repo, "services/other", name="other_agent", tool="ask")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    subprocess.run(["git", "rm", "-q", "-r", "services/gone"], cwd=repo, check=True)
    _commit_all(repo, "remove the gone project")

    evidence = weak_marker_evidence_dirs(
        repo, ["services/gone/pyproject.toml", "services/gone/app/agent.py"]
    )
    assert evidence.causes == {"deleted_evidence"}
    assert [entry.path for entry in evidence.undetermined] == ["services/gone"]

    action = _preview(repo)["control"]["next_action"]
    assert action["actor"] == "human"
    assert action["command"] is None


def test_a_deleted_boundary_beside_a_capped_parse_keeps_both_causes(
    tmp_path: Path,
) -> None:
    """Causes accumulate. Reporting only the first sent a deleted-evidence case
    to a higher-cap retry, which cannot find a file the change removed and
    settles the wrong question confidently (#399 review)."""

    repo = _init_repo(tmp_path)
    gone = repo / "services" / "gone"
    gone.mkdir(parents=True)
    (gone / "requirements.txt").write_text("google-adk\n", encoding="utf-8")
    # Directly in the directory, so they are what the probe spends its budget
    # on and the deletion is a second, independent fact about the same place.
    for index in range(1001):
        (gone / f"aa_mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")
    (gone / "zz_agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="gone_agent", tool="act"), encoding="utf-8"
    )
    _write_agent_project(repo, "services/other", name="other_agent", tool="ask")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    subprocess.run(
        ["git", "rm", "-q", "services/gone/zz_agent.py"], cwd=repo, check=True
    )
    _commit_all(repo, "remove the agent")

    evidence = weak_marker_evidence_dirs(repo, ["services/gone/zz_agent.py"])
    assert evidence.causes == {"parse_budget", "deleted_evidence"}

    # And the route follows the cause a retry cannot answer.
    action = _preview(repo)["control"]["next_action"]
    assert action["actor"] == "human"
    assert action["command"] is None


def test_budget_exhaustion_routes_preview_to_a_bound_that_settles_it(
    tmp_path: Path,
) -> None:
    """A recovery that reruns at the cap it just hit is not a recovery. The
    emitted command carries a bound covering every Python file, so following
    it settles what the capped pass could not (#399 review)."""

    repo = _init_repo(tmp_path)
    flat = repo / "flat"
    flat.mkdir()
    (flat / "requirements.txt").write_text("requests\n", encoding="utf-8")
    for index in range(1205):
        (flat / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")
    _write_agent_project(repo, "other", name="other_agent", tool="ask")
    _commit_all(repo, "base")
    _set_origin_main(repo)
    (flat / "mod00001.py").write_text("x = 2\n", encoding="utf-8")
    _commit_all(repo, "touch a module")

    payload = _preview(repo)
    action = payload["control"]["next_action"]
    assert action["kind"] == "discover"
    assert "--max-python-files" in action["command"]

    # Following it terminates: the reported bound covers every Python file, so
    # the next envelope is not truncated.
    total = int(action["command"].split("--max-python-files ")[1].split()[0])
    settled = runner.invoke(
        app,
        ["detect", "--workspace", str(repo), "--max-python-files", str(total), "--json"],
    )
    assert settled.exit_code == 0, settled.output
    assert json.loads(settled.output)["python_parse_truncated"] is False


def test_the_probe_stops_at_the_same_budget_discovery_stops_at(
    tmp_path: Path,
) -> None:
    """Preview is the lightweight path; it cannot be the one place with no
    parse bound. Exhaustion is undetermined, not negative — the probe would
    otherwise report "no project here" from files it never read, which is
    #395 one surface over (#399 review)."""

    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="wide"), encoding="utf-8"
    )
    flat = repo / "flat"
    flat.mkdir()
    (flat / "requirements.txt").write_text("requests\n", encoding="utf-8")
    for index in range(1205):
        (flat / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")
    _commit_all(repo, "base")

    exhausted = weak_marker_evidence_dirs(repo, ["flat/mod00001.py"])
    assert exhausted.directories == frozenset()
    assert [entry.path for entry in exhausted.undetermined] == ["flat"]
    assert exhausted.causes == {"parse_budget"}

    # Within budget the same directory settles negative, so exhaustion is the
    # only thing the tri-state is reporting.
    assert (
        weak_marker_evidence_dirs(
            repo, ["flat/mod00001.py"], max_python_files=5000
        ).undetermined
        == ()
    )

    # A budget the directory exactly fills is not truncation: the last unit is
    # spent on the last file and nothing stayed unread, which is what `detect`
    # says about the same directory (#399 review).
    exact = weak_marker_evidence_dirs(
        repo, ["flat/mod00001.py"], max_python_files=1205
    )
    assert exact.undetermined == ()


def test_preview_and_detect_name_the_same_project(
    weak_marker_monorepo: Path,
) -> None:
    """One question, one answer. `detect` already reported `smart_closer` as
    a project while preview reported none, and an adopter following the two
    in sequence got contradictory instructions (#394)."""

    changed = ["python/agents/smart_closer/agent.py"]
    resolution = resolve_change_scope(
        root=weak_marker_monorepo,
        changed_files=changed,
        evidence_dirs=weak_marker_evidence_dirs(
            weak_marker_monorepo, changed
        ).directories,
    )
    detected = detect_workspace(weak_marker_monorepo)

    assert resolution.scope is not None
    named = {
        candidate.path: candidate.marker
        for candidate in detected.agent_project_candidates
    }
    assert resolution.scope.relative in named
    assert named[resolution.scope.relative] == resolution.scope.marker


def test_a_requirements_file_without_an_agent_beside_it_is_no_evidence(
    tmp_path: Path,
) -> None:
    """The weak marker stays weak. `docs/requirements.txt` travels with a
    directory that is not a project root, and unlocking it would route
    adoption into `docs/` — strictly worse than the repository root."""

    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="solo"), encoding="utf-8"
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "requirements.txt").write_text("sphinx\n", encoding="utf-8")
    (repo / "docs" / "conf.py").write_text("project = 'solo'\n", encoding="utf-8")
    (repo / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="solo_agent", tool="act"), encoding="utf-8"
    )

    changed = ["docs/conf.py", "agent.py"]
    evidence = weak_marker_evidence_dirs(repo, changed)
    assert evidence.directories == frozenset()
    assert evidence.undetermined == ()
    assert (
        resolve_change_scope(
            root=repo,
            changed_files=changed,
            evidence_dirs=evidence.directories,
        ).status
        == "not_narrowed"
    )


def test_evidence_is_read_from_the_directory_itself_not_from_below_it(
    tmp_path: Path,
) -> None:
    """`detect` derives an evidence directory from an evidence file's own
    parent, so a framework import two levels down names that sub-directory.
    Reading recursively here would draw a boundary `detect` does not."""

    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="solo"), encoding="utf-8"
    )
    deploy = repo / "deploy"
    (deploy / "app").mkdir(parents=True)
    (deploy / "requirements.txt").write_text("google-adk\n", encoding="utf-8")
    (deploy / "main.py").write_text("VERSION = '1'\n", encoding="utf-8")
    (deploy / "app" / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="deployed", tool="act"), encoding="utf-8"
    )

    assert (
        weak_marker_evidence_dirs(repo, ["deploy/app/agent.py"]).directories
        == frozenset()
    )
    assert (
        weak_marker_evidence_dirs(repo, ["deploy/main.py"]).directories == frozenset()
    )


def test_a_strong_marker_needs_no_evidence_at_all(tmp_path: Path) -> None:
    """Only directories whose answer can change are examined: a project root
    that already carries `pyproject.toml` is one without any evidence, and a
    directory carrying no weak marker cannot become one because of it."""

    repo = _init_repo(tmp_path)
    project = repo / "services" / "billing"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        _PYPROJECT.format(name="billing"), encoding="utf-8"
    )
    (project / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="billing_agent", tool="charge"), encoding="utf-8"
    )

    changed = ["services/billing/agent.py"]
    evidence = weak_marker_evidence_dirs(repo, changed)
    assert evidence.directories == frozenset()
    assert evidence.undetermined == ()
    scope = resolve_change_scope(
        root=repo,
        changed_files=changed,
        evidence_dirs=evidence.directories,
    ).scope
    assert scope is not None
    assert scope.relative == "services/billing"
    assert scope.marker == "pyproject.toml"


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


def test_detect_publishes_the_init_command_for_each_candidate(monorepo: Path) -> None:
    """The decision is a person's; carrying it out is not.

    `detect` published the choice as a JSON selector inside a shell command —
    ``init --workspace <agent_project_candidates[].path> --write`` — and no
    runnable command at all, so an agent following `allowed_next_commands` had
    nowhere to go (#397). `init`'s own refusal has always ranked the decision
    first and the per-candidate commands below it; `detect` now emits the same
    shape."""

    result = runner.invoke(app, ["detect", "--workspace", str(monorepo), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    actions = payload["next_actions"]
    # Rank 1 stays the decision: promoting one candidate would make the same
    # arbitrary pick `init --write` refuses to make.
    assert actions[0]["kind"] == "review"
    assert actions[0]["command"] is None
    commands = [action for action in actions[1:] if action["kind"] == "command"]
    assert [action["command"] for action in commands] == [
        _init_command(monorepo / "python/agents/RAG"),
        _init_command(monorepo / "python/agents/crypto-payroll-agent"),
    ]
    # Structured argv, not only a string a caller has to re-parse (#369).
    assert commands[0]["args"] == [
        "init",
        "--workspace",
        str(monorepo / "python/agents/RAG"),
        "--write",
        "--json",
    ]
    assert "ask_rag_agent" in commands[0]["why"]


def test_detect_and_init_publish_the_same_candidate_commands(monorepo: Path) -> None:
    """Two commands an adopter runs in sequence must not disagree about the
    recovery for one workspace.

    Flagless on both sides, which is the whole of the claim: `init` repeats the
    setup its invocation asked for, and `detect` was asked for none — see
    :func:`test_detect_does_not_invent_setup_flags_init_was_asked_for`."""

    detected = runner.invoke(app, ["detect", "--workspace", str(monorepo), "--json"])
    assert detected.exit_code == 0, detected.output
    refused = runner.invoke(
        app, ["init", "--workspace", str(monorepo), "--write", "--json"]
    )
    assert refused.exit_code == 2, refused.output

    def commands(payload: dict) -> list[str]:
        return [
            action["command"]
            for action in payload["next_actions"]
            if action["kind"] == "command"
        ]

    published = commands(json.loads(detected.output))
    # Not `[] == []`: a regression that drops the commands from the shared
    # builder drops them from both callers at once, and equality alone would
    # report that green.
    assert published == [
        _init_command(monorepo / "python/agents/RAG"),
        _init_command(monorepo / "python/agents/crypto-payroll-agent"),
    ]
    assert published == commands(json.loads(refused.output))


def test_detect_does_not_invent_setup_flags_init_was_asked_for(
    monorepo: Path,
) -> None:
    """The one place the two lists legitimately differ.

    `init`'s recovery repeats the flags its own invocation carried, because a
    recovery that silently drops `--ci` completes with less than the caller
    requested and reports success for it. `detect` asked for no setup at all,
    so promising any would be inventing it — and a reader told the lists are
    identical would substitute the flagless command for the one they need."""

    detected = json.loads(
        runner.invoke(app, ["detect", "--workspace", str(monorepo), "--json"]).output
    )
    refused = runner.invoke(
        app, ["init", "--workspace", str(monorepo), "--write", "--ci", "--json"]
    )
    assert refused.exit_code == 2, refused.output

    def first_command(payload: dict) -> str:
        return next(
            action["command"]
            for action in payload["next_actions"]
            if action["kind"] == "command"
        )

    assert first_command(detected).endswith("--write --json")
    assert first_command(json.loads(refused.output)).endswith("--write --ci --json")


def test_following_a_detect_candidate_command_adopts_that_project(
    monorepo: Path,
) -> None:
    """A published step is only a step if running it changes the answer."""

    detected = runner.invoke(app, ["detect", "--workspace", str(monorepo), "--json"])
    assert detected.exit_code == 0, detected.output
    first = next(
        action
        for action in json.loads(detected.output)["next_actions"]
        if action["kind"] == "command"
    )

    written = runner.invoke(app, first["args"])

    assert written.exit_code == 0, written.output
    assert (monorepo / "python/agents/RAG" / "shipgate.yaml").is_file()
    # Scoped, not folded into a root manifest for every agent in the repository.
    assert not (monorepo / "shipgate.yaml").exists()


def test_detect_never_offers_the_workspace_root_as_a_candidate_command(
    monorepo: Path,
) -> None:
    """`.` is the scope `init` would refuse, so a command naming it returns
    here. It stays in the reported candidate list — unattributed agent files
    are real evidence of why the answer is unresolved — but not in the
    routing."""

    (monorepo / "loose_agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="loose_agent", tool="loose"), encoding="utf-8"
    )
    _commit_all(monorepo, "an agent that belongs to no project")

    payload = json.loads(
        runner.invoke(app, ["detect", "--workspace", str(monorepo), "--json"]).output
    )

    assert "." in [candidate["path"] for candidate in payload["agent_project_candidates"]]
    commands = [
        action["command"]
        for action in payload["next_actions"]
        if action["kind"] == "command"
    ]
    # Named exactly, so an empty list cannot satisfy the exclusion below.
    assert commands == [
        _init_command(monorepo / "python/agents/RAG"),
        _init_command(monorepo / "python/agents/crypto-payroll-agent"),
    ]
    assert all(f"--workspace {monorepo} --write" not in command for command in commands)


def test_detect_offers_no_candidate_command_under_a_stop(tmp_path: Path) -> None:
    """The per-candidate commands carry out one decision; they must not ride
    under a different one.

    A workspace whose only agent evidence is two nested manifests classifies as
    a non-agent library — `stop`, `setup_not_applicable` — while still holding
    two candidate scopes. Publishing "not a Shipgate target" and then an
    `init --write` for each candidate is two answers to two different
    questions in one ranked list (#397)."""

    repo = _init_repo(tmp_path)
    (repo / "pyproject.toml").write_text(_PYPROJECT.format(name="plain-lib"), encoding="utf-8")
    (repo / "lib.py").write_text("def hello() -> int:\n    return 1\n", encoding="utf-8")
    for name in ("a", "b"):
        project = repo / "apps" / name
        project.mkdir(parents=True)
        (project / "shipgate.yaml").write_text(
            f"schema_version: 0.1\nagent:\n  name: {name}\n"
            '  declared_purpose: ["x"]\n',
            encoding="utf-8",
        )
        (project / "mod.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    _commit_all(repo, "two adopted projects, no agent code")

    payload = json.loads(
        runner.invoke(app, ["detect", "--workspace", str(repo), "--json"]).output
    )

    assert payload["agent_scope"] == "ambiguous"
    assert payload["control"]["decision"] == "setup_not_applicable"
    assert [action["kind"] for action in payload["next_actions"]] == ["stop"]


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
    # "unknown" is the one-or-fewer-candidates form of the same truncation,
    # so it carries the flag too rather than being its own separate story.
    assert capped.agent_scope_truncated is True
    assert detect_workspace(repo, max_python_files=5000).agent_scope == "ambiguous"

    result = runner.invoke(app, ["init", "--workspace", str(repo), "--write", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["manifest_status"] == "refused_unresolved_scope"
    assert payload["auto_detected"]["agent_scope"] == "unknown"
    assert not (repo / "shipgate.yaml").exists()


def test_a_truncated_candidate_list_is_never_reported_as_complete(
    tmp_path: Path,
) -> None:
    """Truncation and ambiguity are independent facts, and the `ambiguous`
    check used to short-circuit the truncation one. The result was that the
    cap warning was reachable only where one or fewer projects were found —
    never on the large repositories the cap had actually cut — and an
    adopter was handed an authoritative-looking list their own project was
    missing from (#395)."""

    repo = tmp_path / "capped"
    # `aa_*` sort before the filler and are parsed; `zz_hidden` sorts after
    # it and is not. It is the project the cap hides.
    for name in ("aa_one", "aa_two", "zz_hidden"):
        project = repo / name
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            _PYPROJECT.format(name=name.replace("_", "-")), encoding="utf-8"
        )
        (project / "agent.py").write_text(
            _ADK_AGENT_MODULE.format(name=name, tool="act"), encoding="utf-8"
        )
    filler = repo / "mm_filler"
    filler.mkdir()
    for index in range(1200):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")

    result = detect_workspace(repo)

    assert result.agent_scope == "ambiguous"
    assert result.agent_scope_truncated is True
    listed = [candidate.path for candidate in result.agent_project_candidates]
    assert listed == ["aa_one", "aa_two"]
    assert "zz_hidden" not in listed
    # The uncapped census is what bounds the claim: three marked projects
    # plus the workspace itself, which is a candidate scope whether or not it
    # carries a marker. Two of the four are listed.
    assert result.workspace_signals.project_root_count == 4
    assert "--max-python-files" in result.next_action

    # Raising the cap reaches the hidden project, and the claim of
    # completeness comes back with it.
    full = detect_workspace(repo, max_python_files=5000)
    assert full.agent_scope == "ambiguous"
    assert full.agent_scope_truncated is False
    assert "zz_hidden" in [c.path for c in full.agent_project_candidates]
    assert "--max-python-files" not in full.next_action

    refused = runner.invoke(app, ["init", "--workspace", str(repo), "--write", "--json"])
    assert refused.exit_code == 2, refused.output
    payload = json.loads(refused.output)
    assert payload["manifest_status"] == "refused_unresolved_scope"
    assert payload["auto_detected"]["agent_scope"] == "ambiguous"
    assert payload["auto_detected"]["agent_scope_truncated"] is True
    assert "may be incomplete" in payload["manifest_message"]
    assert "--max-python-files" in payload["manifest_message"]
    assert not (repo / "shipgate.yaml").exists()


def test_a_capped_walk_never_reports_no_agent_project(tmp_path: Path) -> None:
    """The same lie one step earlier. When the cap lands before any agent
    file is reached, "does not appear to be an agent project" is a claim
    about a workspace the walk read part of — and it is the reading that
    stops an adopter (#395)."""

    repo = tmp_path / "capped-negative"
    # Both projects sort after the filler, so nothing fires at all.
    for name in ("zz_one", "zz_two"):
        project = repo / name
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            _PYPROJECT.format(name=name.replace("_", "-")), encoding="utf-8"
        )
        (project / "agent.py").write_text(
            _ADK_AGENT_MODULE.format(name=name, tool="act"), encoding="utf-8"
        )
    filler = repo / "aa_filler"
    filler.mkdir()
    for index in range(1200):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")

    result = detect_workspace(repo)
    assert result.is_agent_project is False
    assert result.agent_scope == "unknown"
    assert result.agent_scope_truncated is True

    echoed = runner.invoke(app, ["detect", "--workspace", str(repo)])
    assert echoed.exit_code == 0, echoed.output
    assert "does not appear to be an agent project" not in echoed.output
    assert "capped" in echoed.output
    assert "--max-python-files" in echoed.output


def test_an_unmarked_root_agent_is_counted_before_the_cap_clears_the_walk(
    tmp_path: Path,
) -> None:
    """The census has to leave room for the scope the walk falls back to.
    Counting only marker directories missed the workspace itself, so a
    repository with one marked sub-project and an unmarked root agent past the
    cap censused a single root, kept `single` with no truncation warning, and
    let `init --write` write a root manifest carrying the sub-project's agent
    name while covering the root agent nobody had read (#399 review)."""

    repo = tmp_path / "implicit"
    nested = repo / "aa_nested"
    nested.mkdir(parents=True)
    (nested / "pyproject.toml").write_text(
        _PYPROJECT.format(name="aa-nested"), encoding="utf-8"
    )
    (nested / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="nested_agent", tool="act"), encoding="utf-8"
    )
    # Sorts after the filler, so the cap hides it; it carries no marker, so it
    # is the workspace-fallback candidate `.`.
    (repo / "zz_root_agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="root_agent_unmarked", tool="act"),
        encoding="utf-8",
    )
    filler = repo / "mm_filler"
    filler.mkdir()
    for index in range(1200):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")

    capped = detect_workspace(repo)
    assert capped.workspace_signals.project_root_count == 2
    assert capped.agent_scope_truncated is True
    assert capped.agent_scope != "single"

    # Which is the answer the uncapped walk gives, arrived at honestly.
    full = detect_workspace(repo, max_python_files=5000)
    assert full.agent_scope == "ambiguous"
    assert [c.path for c in full.agent_project_candidates] == [".", "aa_nested"]

    refused = runner.invoke(app, ["init", "--workspace", str(repo), "--write", "--json"])
    assert refused.exit_code == 2, refused.output
    assert not (repo / "shipgate.yaml").exists()


def test_a_single_scope_workspace_still_reports_its_capped_parse(
    tmp_path: Path,
) -> None:
    """`agent_scope_truncated` is the wrong guard for a claim about the
    workspace. It also requires more than one candidate scope, because with
    one there is nowhere for a second *project* to hide — but there is still
    somewhere for the workspace's only *agent* to hide. A root
    `pyproject.toml` repo whose agent sorts past the cap reported
    `agent_scope: "single"`, `agent_scope_truncated: false`, and a terminal
    `SHIP-DIAG-NON-AGENT-LIBRARY` / `setup_not_applicable` / `stop` (#399
    review)."""

    repo = tmp_path / "single-capped"
    filler = repo / "aa_filler"
    filler.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="single-capped"), encoding="utf-8"
    )
    for index in range(1001):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "zz_agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="hidden_agent", tool="act"), encoding="utf-8"
    )

    result = detect_workspace(repo)
    assert result.is_agent_project is False
    assert result.agent_scope == "single"
    # The narrow flag is correctly false — and useless as a guard here.
    assert result.agent_scope_truncated is False
    assert result.python_parse_truncated is True

    payload = json.loads(
        runner.invoke(app, ["detect", "--workspace", str(repo), "--json"]).output
    )
    assert [d["id"] for d in payload["diagnostics"]] == []
    assert payload["control"]["decision"] != "setup_not_applicable"
    assert payload["control"]["next_action"]["kind"] == "discover"

    # Raising the cap finds the agent the terminal negative denied.
    total = result.workspace_signals.python_file_total
    assert detect_workspace(repo, max_python_files=total).is_agent_project is True


def _capped_single_project(repo: Path, *, extra: str | None = None) -> None:
    """One project root, 1,001 inert modules first, the agent last."""

    filler = repo / "aa_filler"
    filler.mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        _PYPROJECT.format(name="capped"), encoding="utf-8"
    )
    for index in range(1001):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "zz_agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="hidden_agent", tool="act"), encoding="utf-8"
    )
    if extra is not None:
        (repo / extra).write_text(
            "openapi: 3.0.0\n"
            "info: {title: root, version: 1.0.0}\n"
            "paths:\n"
            "  /pay:\n"
            "    post:\n"
            "      operationId: pay\n"
            "      summary: Send money.\n"
            '      responses: {"200": {description: ok}}\n',
            encoding="utf-8",
        )


def test_init_refuses_to_write_a_manifest_from_a_capped_parse(
    tmp_path: Path,
) -> None:
    """`init` runs its own discovery, so the bound `detect` settled on does not
    reach it. Following the recommended route landed on `init --write`, which
    re-ran at the default cap, missed the agent, and wrote a `CHANGE_ME`
    manifest with no tools at exit 0 (#399 review)."""

    repo = tmp_path / "capped-init-write"
    _capped_single_project(repo)

    refused = runner.invoke(app, ["init", "--workspace", str(repo), "--write", "--json"])
    assert refused.exit_code == 2, refused.output
    payload = json.loads(refused.output)
    assert payload["manifest_status"] == "refused_unresolved_scope"
    assert payload["auto_detected"]["python_parse_truncated"] is True
    assert not (repo / "shipgate.yaml").exists()

    # Rank 1 is this same setup at a bound that settles the scan, so one step
    # both finishes the parse and does what the caller asked for.
    first = payload["next_actions"][0]
    total = payload["auto_detected"]["workspace_signals"]["python_file_total"]
    assert first["kind"] == "command"
    assert f"--max-python-files {total}" in first["command"]

    settled = runner.invoke(
        app,
        [
            "init",
            "--workspace",
            str(repo),
            "--write",
            "--max-python-files",
            str(total),
            "--json",
        ],
    )
    assert settled.exit_code == 0, settled.output
    after = json.loads(settled.output)
    assert after["manifest_status"] == "written"
    assert after["auto_detected"]["agent_name"] == "hidden_agent"


def test_a_settled_scope_does_not_imply_a_complete_parse(tmp_path: Path) -> None:
    """A single scope settles the manifest *boundary* and says nothing about
    whether the tool surface that manifest would declare was read. Gating the
    artifact nudge on scope alone let it outrank the full-count retry and adopt
    a truncated surface (#399 review)."""

    repo = tmp_path / "capped-artifact"
    _capped_single_project(repo, extra="openapi.yaml")

    payload = json.loads(
        runner.invoke(app, ["detect", "--workspace", str(repo), "--json"]).output
    )
    assert payload["agent_scope"] == "single"
    assert payload["python_parse_truncated"] is True
    assert payload["suggested_sources"]
    assert [d["id"] for d in payload["diagnostics"]] == []
    command = payload["control"]["next_action"]["command"]
    assert "init" not in command
    assert "--max-python-files" in command


def test_the_library_next_action_never_ends_at_a_capped_negative(
    tmp_path: Path,
) -> None:
    """`DetectResult.next_action` is what the zero-install path and every
    library consumer read; the CLI replaces it with the routed one, which is
    why the stale branch survived. A single-scope capped workspace returned
    "Workspace does not appear to be an agent project. No action." (#399
    review)."""

    repo = tmp_path / "capped-library"
    _capped_single_project(repo)

    result = detect_workspace(repo)
    assert result.is_agent_project is False
    assert result.python_parse_truncated is True
    assert "does not appear to be an agent project" not in result.next_action
    total = result.workspace_signals.python_file_total
    assert f"--max-python-files {total}" in result.next_action
    assert detect_workspace(repo, max_python_files=total).is_agent_project is True


def test_an_artifact_nudge_never_names_an_init_that_would_refuse(
    tmp_path: Path,
) -> None:
    """Emitting no negative diagnostic is not enough: setup routing ranks a
    diagnostic ahead of the advance, so the artifact-only nudge published a
    root `init --write` over the top of the scope route — and that exact
    command exits `refused_unresolved_scope` (#399 review)."""

    repo = tmp_path / "artifact-capped"
    filler = repo / "aa_filler"
    filler.mkdir(parents=True)
    (repo / "openapi.yaml").write_text(
        "openapi: 3.0.0\n"
        "info: {title: root, version: 1.0.0}\n"
        "paths:\n"
        "  /pay:\n"
        "    post:\n"
        "      operationId: pay\n"
        "      summary: Send money.\n"
        '      responses: {"200": {description: ok}}\n',
        encoding="utf-8",
    )
    for index in range(1001):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")
    nested = repo / "zz_nested"
    nested.mkdir()
    (nested / "pyproject.toml").write_text(
        _PYPROJECT.format(name="zz-nested"), encoding="utf-8"
    )
    (nested / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="nested_agent", tool="act"), encoding="utf-8"
    )

    payload = json.loads(
        runner.invoke(app, ["detect", "--workspace", str(repo), "--json"]).output
    )
    assert payload["agent_scope"] == "unknown"
    assert payload["suggested_sources"]
    assert "SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY" not in [
        d["id"] for d in payload["diagnostics"]
    ]
    command = payload["control"]["next_action"]["command"]
    assert "init" not in command
    assert "--max-python-files" in command

    # The command it used to publish, run explicitly, is the refusal.
    refused = runner.invoke(app, ["init", "--workspace", str(repo), "--write", "--json"])
    assert refused.exit_code == 2, refused.output
    assert json.loads(refused.output)["manifest_status"] == "refused_unresolved_scope"


def test_a_capped_init_refusal_leads_with_the_retry_not_a_human_choice(
    tmp_path: Path,
) -> None:
    """An `unknown` scope is not a choice nobody made — it is a scan nobody
    finished. Raising the cap is mechanical and read-only, so it is rank 1;
    the human choice waits until there is a settled list to choose from
    (#399 review)."""

    repo = tmp_path / "capped-init"
    filler = repo / "aa_filler"
    filler.mkdir(parents=True)
    for index in range(1001):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")
    nested = repo / "zz_nested"
    nested.mkdir()
    (nested / "pyproject.toml").write_text(
        _PYPROJECT.format(name="zz-nested"), encoding="utf-8"
    )
    (nested / "agent.py").write_text(
        _ADK_AGENT_MODULE.format(name="nested_agent", tool="act"), encoding="utf-8"
    )

    refused = runner.invoke(app, ["init", "--workspace", str(repo), "--write", "--json"])
    assert refused.exit_code == 2, refused.output
    payload = json.loads(refused.output)
    assert payload["auto_detected"]["agent_scope"] == "unknown"
    first = payload["next_actions"][0]
    assert first["kind"] == "command"
    total = payload["auto_detected"]["workspace_signals"]["python_file_total"]
    assert f"--max-python-files {total}" in first["command"]
    assert payload["control"]["next_action"]["actor"] == "coding_agent"
    assert not (repo / "shipgate.yaml").exists()


def test_a_capped_walk_never_publishes_a_terminal_machine_route(
    tmp_path: Path,
) -> None:
    """The human summary was only half of it. Every negative-control
    diagnostic publishes a `stop`, which routing turns into
    `setup_not_applicable` — a terminal machine route for a scan that said it
    was inconclusive. `bootstrap` read the same negative as "nothing to do"
    (#399 review)."""

    repo = tmp_path / "capped-machine"
    for name in ("zz_one", "zz_two"):
        project = repo / name
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            _PYPROJECT.format(name=name.replace("_", "-")), encoding="utf-8"
        )
        (project / "agent.py").write_text(
            _ADK_AGENT_MODULE.format(name=name, tool="act"), encoding="utf-8"
        )
    filler = repo / "aa_filler"
    filler.mkdir()
    for index in range(1200):
        (filler / f"mod{index:05d}.py").write_text("x = 1\n", encoding="utf-8")

    detected = runner.invoke(app, ["detect", "--workspace", str(repo), "--json"])
    assert detected.exit_code == 0, detected.output
    payload = json.loads(detected.output)
    assert payload["is_agent_project"] is False
    assert payload["python_parse_truncated"] is True
    assert [d["id"] for d in payload["diagnostics"]] == []
    assert payload["control"]["decision"] != "setup_not_applicable"

    # And the route is executable. Raising a cap needs no decision, so
    # publishing it as prose inside a human route left the only actionable
    # step in a string, on a payload saying `command: null` (#399 review).
    action = payload["control"]["next_action"]
    assert action["actor"] == "coding_agent"
    assert action["kind"] == "discover"
    total = payload["workspace_signals"]["python_file_total"]
    assert f"--max-python-files {total}" in action["command"]

    # Following it settles the question rather than reproducing it.
    settled = runner.invoke(
        app,
        [
            "detect",
            "--workspace",
            str(repo),
            "--max-python-files",
            str(total),
            "--json",
        ],
    )
    assert settled.exit_code == 0, settled.output
    after = json.loads(settled.output)
    assert after["python_parse_truncated"] is False
    assert after["is_agent_project"] is True
    assert [c["path"] for c in after["agent_project_candidates"]] == [
        "zz_one",
        "zz_two",
    ]


def test_an_uncapped_ambiguous_workspace_claims_no_truncation(
    monorepo: Path,
) -> None:
    """Negative control: the caveat has to be absent when the walk was
    complete, or it says nothing when it is present."""

    result = detect_workspace(monorepo)

    assert result.agent_scope == "ambiguous"
    assert result.agent_scope_truncated is False
    assert "--max-python-files" not in result.next_action

    refused = runner.invoke(
        app, ["init", "--workspace", str(monorepo), "--write", "--json"]
    )
    assert refused.exit_code == 2, refused.output
    payload = json.loads(refused.output)
    assert payload["auto_detected"]["agent_scope_truncated"] is False
    assert "may be incomplete" not in payload["manifest_message"]


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
