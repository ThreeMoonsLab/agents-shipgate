"""First applicability must not discard an existing host review workflow."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.bootstrap import bootstrap_run
from agents_shipgate.cli.discovery.host_boundary import discover_host_boundary
from agents_shipgate.cli.discovery.signals import detect_workspace
from agents_shipgate.cli.main import app
from agents_shipgate.core import host_grants
from agents_shipgate.core.boundary_registry import (
    BOUNDARY_ADAPTERS,
    boundary_adapters_for_path,
    host_config_adapters_for_path,
)
from tests.test_zero_install_detector import _load_script_module

CONFIG_PATHS = [
    ".claude/settings.json", ".claude/settings.local.json", ".mcp.json",
    ".codex/config.toml", ".codex/hooks.json", ".codex/requirements.toml",
    ".cursor/cli.json", ".cursor/mcp.json", ".vscode/mcp.json",
    "nested/.mcp.json", "nested/.codex/config.toml",
]


def write(root: Path, path: str, text: str = "{}") -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def payload(*args: str) -> dict:
    result = CliRunner().invoke(app, list(args))
    assert result.exit_code == 0, (result.output, result.exception)
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def zero():
    return _load_script_module()


@pytest.mark.parametrize("path", CONFIG_PATHS)
@pytest.mark.parametrize("text", ["{}", "{broken"])
def test_host_config_has_a_read_only_route_without_tool_exports(tmp_path, zero, path, text):
    write(tmp_path, path, text)
    canonical = detect_workspace(tmp_path)
    standalone = zero.detect(tmp_path)
    assert not canonical.is_agent_project
    assert canonical.suggested_sources == []
    candidates = [c.model_dump() for c in canonical.host_boundary_candidates]
    assert [c["path"] for c in candidates] == [path]
    assert standalone["host_boundary_candidates"] == candidates
    assert standalone["host_discovery_incomplete_paths"] == []
    result = payload("detect", "--workspace", str(tmp_path), "--json")
    control = result["control"]
    assert control["decision"] == "setup_incomplete"
    assert control["control_state"] == "agent_action_required"
    assert not any(control["permissions"].values())
    argv = shlex.split(control["next_action"]["command"])
    assert "audit" in argv and "--host" in argv
    assert argv[argv.index("--workspace") + 1] == str(tmp_path.resolve())
    assert not any(a["kind"] == "stop" for a in result["next_actions"])


@pytest.mark.parametrize("flags", [[], ["--ci"], ["--ci", "--agent-instructions=all"], ["--claude-code"], ["--local-review"]])
def test_direct_init_hands_off_without_writing_any_setup(tmp_path, flags):
    write(tmp_path, ".claude/settings.json")
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    args = ["init", "--workspace", str(tmp_path), "--json", *flags]
    if "--local-review" not in flags:
        args.append("--write")
    result = payload(*args)
    assert result["manifest_status"] == "not_applicable_host_review"
    assert result["placeholders"] == []
    assert not any(result["control"]["permissions"].values())
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before


def test_bootstrap_hands_off_the_detect_route_without_init(tmp_path):
    write(tmp_path, ".claude/settings.json")
    result = bootstrap_run(workspace=tmp_path, ci=True, apply=True)
    assert result["verdict"] == "host_review_required"
    assert [s["label"] for s in result["steps"]] == ["detect"]
    assert result["release_decision"] is None
    assert "audit" in shlex.split(result["control"]["next_action"]["command"])
    assert not (tmp_path / "shipgate.yaml").exists()
    assert not (tmp_path / ".github").exists()


@pytest.mark.parametrize("path", ["AGENTS.md", "CLAUDE.md", ".github/workflows/test.yml", "policies/example.yaml", "nested/.claude/settings.json", "nested/.cursor/mcp.json", "nested/.vscode/mcp.json"])
def test_unrelated_and_unsupported_paths_do_not_prove_host_adoption(tmp_path, zero, path):
    write(tmp_path, path)
    result = payload("detect", "--workspace", str(tmp_path), "--json")
    assert result["host_boundary_candidates"] == []
    assert result["control"]["decision"] == "setup_not_applicable"
    assert zero.detect(tmp_path)["host_boundary_candidates"] == []


def test_ignored_host_settings_are_seen_by_both_paths(tmp_path, zero):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    write(tmp_path, ".gitignore", ".claude/\n")
    write(tmp_path, ".claude/settings.json")
    assert detect_workspace(tmp_path).host_boundary_candidates
    assert zero.detect(tmp_path)["host_boundary_candidates"]


def test_mixed_tool_source_preserves_the_builder_route(tmp_path, zero):
    write(tmp_path, ".claude/settings.json")
    write(tmp_path, "tools/mcp-tools.json", '{"tools":[{"name":"lookup","description":"Read","inputSchema":{"type":"object"}}]}')
    result = payload("detect", "--workspace", str(tmp_path), "--json")
    assert result["suggested_sources"] and result["host_boundary_candidates"]
    assert "init" in shlex.split(result["control"]["next_action"]["command"])
    assert "init" in shlex.split(zero.detect(tmp_path)["next_action"])


def test_zero_install_human_output_keeps_mixed_source_candidates(tmp_path, zero, capsys):
    write(tmp_path, ".claude/settings.json")
    write(tmp_path, "tools/mcp-tools.json", '{"tools":[{"name":"lookup","description":"Read","inputSchema":{"type":"object"}}]}')
    assert zero.main(["--workspace", str(tmp_path)]) == 0
    assert "tools/mcp-tools.json" in capsys.readouterr().out


@pytest.mark.parametrize("facade", ["reparse", "junction"])
def test_zero_install_refuses_junction_before_enumerating_descendants(tmp_path, zero, monkeypatch, facade):
    from types import SimpleNamespace
    linked = tmp_path / "linked"
    linked.mkdir()
    write(linked, ".mcp.json")
    original_lstat, original_scandir = Path.lstat, os.scandir
    visited = []
    def lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if facade == "reparse" and path == linked:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    def scandir(path):
        visited.append(Path(path))
        return original_scandir(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(Path, "is_junction", lambda path: facade == "junction" and path == linked, raising=False)
    monkeypatch.setattr(os, "scandir", scandir)
    # Refused, and the refusal is published as an unseen subject rather than
    # as an empty census: nothing beneath the facade was enumerated, and no
    # candidate is claimed from a walk that stopped.
    assert zero._discover_host_boundary(tmp_path) == ([], ["linked"])
    assert linked not in visited


def test_python_cap_still_outranks_host_route(tmp_path):
    write(tmp_path, ".claude/settings.json")
    write(tmp_path, "one.py", "print(1)\n")
    write(tmp_path, "two.py", "from agents import Agent\nagent = Agent(name='Support')\n")
    result = payload("detect", "--workspace", str(tmp_path), "--max-python-files", "1", "--json")
    assert result["python_parse_truncated"]
    assert "detect" in shlex.split(result["control"]["next_action"]["command"])


def test_existing_manifest_keeps_doctor_route(tmp_path):
    write(tmp_path, ".claude/settings.json")
    write(tmp_path, "shipgate.yaml", "bad: manifest")
    result = payload("detect", "--workspace", str(tmp_path), "--json")
    assert "doctor" in shlex.split(result["control"]["next_action"]["command"])


@pytest.mark.parametrize("path", [".mcp.json", ".claude/settings.json"])
def test_config_directory_is_retained_and_routes_to_inspection(tmp_path, zero, path):
    (tmp_path / path).mkdir(parents=True)
    result = payload("detect", "--workspace", str(tmp_path), "--json")
    assert result["host_boundary_candidates"] == zero.detect(tmp_path)["host_boundary_candidates"]
    assert result["host_boundary_candidates"][0]["file_type"] == "directory"
    assert result["control"]["next_action"]["actor"] == "human"
    assert result["control"]["next_action"]["command"] is None
    assert path in result["control"]["next_action"]["why"]


@pytest.mark.parametrize("link", ["images", ".claude", ".mcp.json"])
def test_links_never_establish_an_empty_complete_negative(tmp_path, zero, link):
    try:
        (tmp_path / link).symlink_to(tmp_path / "absent", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    result = payload("detect", "--workspace", str(tmp_path), "--json")
    standalone = zero.detect(tmp_path)
    assert result["host_discovery_incomplete_paths"] == standalone["host_discovery_incomplete_paths"] == [link]
    assert result["host_boundary_candidates"] == standalone["host_boundary_candidates"]
    assert result["control"]["decision"] == "setup_incomplete"
    if link == "images":
        assert result["host_boundary_candidates"] == []
        assert result["control"]["next_action"]["actor"] == "human"


def test_census_does_not_open_nonregular_config(tmp_path, zero):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unavailable")
    os.mkfifo(tmp_path / ".mcp.json")
    candidates, incomplete = discover_host_boundary(tmp_path)
    assert candidates[0].file_type == "other" and not incomplete
    assert zero._discover_host_boundary(tmp_path)[0][0]["file_type"] == "other"


def test_bounded_census_failure_is_not_an_empty_result(tmp_path, monkeypatch, zero):
    write(tmp_path, "asset.txt")
    monkeypatch.setattr(host_grants, "MAX_HOST_REPOSITORY_ENTRIES", 0)
    monkeypatch.setattr(zero, "MAX_HOST_REPOSITORY_ENTRIES", 0)
    assert discover_host_boundary(tmp_path) == ([], ["."])
    assert zero._discover_host_boundary(tmp_path) == ([], ["."])


def test_census_reconfirms_identity_before_publishing(tmp_path, monkeypatch):
    write(tmp_path, ".mcp.json")
    def fail(_self):
        raise ValueError("snapshot moved")
    monkeypatch.setattr(host_grants.HostStaticParseCache, "finish", fail)
    # No candidate survives a census that could not confirm what it read, and
    # the failure is published as an unseen subject rather than swallowed.
    assert discover_host_boundary(tmp_path) == ([], ["."])


def test_an_unreadable_directory_does_not_refuse_the_classification(tmp_path, zero):
    """`detect` must not be stricter than the audit it routes to.

    `audit --host` records exactly this failure as an `inventory_failures` row
    and carries on, so aborting discovery made one `chmod 000` directory an
    unrecoverable exit 4 for `detect`, `init` and `bootstrap` on a repository
    the audit still audits (PR #614 review).
    """
    write(tmp_path, "agent.py", "from agents import Agent\nagent = Agent(name='S')\n")
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "x.txt").write_text("x", encoding="utf-8")
    secret.chmod(0o000)
    try:
        if os.access(secret, os.R_OK):
            pytest.skip("directory permissions are not enforced here")
        canonical = detect_workspace(tmp_path)
        assert canonical.is_agent_project
        assert canonical.host_boundary_candidates == []
        assert canonical.host_discovery_incomplete_paths == ["secret"]
        assert zero.detect(tmp_path)["host_discovery_incomplete_paths"] == ["secret"]

        result = payload("detect", "--workspace", str(tmp_path), "--json")
        assert result["is_agent_project"]
        # An unseen path is recorded, and it does not become the route: an
        # adoptable workspace still routes to `init`.
        assert "init" in shlex.split(result["control"]["next_action"]["command"])
        subjects = [e["subject"] for e in result["surface_exclusions"]["entries"]]
        assert subjects == ["secret"]
    finally:
        secret.chmod(0o755)


def test_an_unreadable_directory_withholds_the_complete_negative(tmp_path, zero):
    write(tmp_path, "notes.txt", "x")
    secret = tmp_path / "secret"
    secret.mkdir()
    secret.chmod(0o000)
    try:
        if os.access(secret, os.R_OK):
            pytest.skip("directory permissions are not enforced here")
        result = payload("detect", "--workspace", str(tmp_path), "--json")
        assert result["host_discovery_incomplete_paths"] == ["secret"]
        assert result["control"]["decision"] == "setup_incomplete"
        assert result["control"]["next_action"]["actor"] == "human"
        assert "secret" in result["control"]["next_action"]["why"]
        assert not result["diagnostics"]

        from agents_shipgate.triggers import evaluate
        verdict = evaluate(paths=["unknown.file"], detect_result=result)
        assert not verdict["stop_conditions_fired"]

        boot = bootstrap_run(workspace=tmp_path, ci=True, apply=True)
        assert boot["verdict"] == "host_review_required"
        assert not (tmp_path / "shipgate.yaml").exists()
    finally:
        secret.chmod(0o755)


def test_a_truncated_subject_list_says_how_many_it_left_out(tmp_path, zero):
    links = [f"link{index}" for index in range(9)]
    (tmp_path / "real").mkdir()
    try:
        for name in links:
            (tmp_path / name).symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    result = payload("detect", "--workspace", str(tmp_path), "--json")
    assert sorted(result["host_discovery_incomplete_paths"]) == sorted(links)
    why = result["control"]["next_action"]["why"]
    # Five names and a full stop asserted there were five (#397).
    assert "(and 4 more)" in why


def test_zero_install_eligibility_matches_the_actual_registry(zero):
    paths = set(CONFIG_PATHS)
    for adapter in BOUNDARY_ADAPTERS:
        paths.update(adapter.exact_paths)
        paths.update(pattern.replace("**/", "nested/").replace("*", "example.json") for pattern in adapter.globs)
    paths |= {"nested/" + p for p in paths}
    paths |= {p.upper() for p in paths}
    paths |= {p.replace("/", "\\") for p in paths}
    for path in paths:
        expected = sorted({h for a in host_config_adapters_for_path(path) for h in a.hosts})
        assert zero._host_config_hosts(path) == expected, path


def test_bootstrap_cli_handoff_is_successful_setup_with_no_authority(tmp_path):
    write(tmp_path, ".claude/settings.json")
    result = payload("bootstrap", "--workspace", str(tmp_path), "--json")
    assert result["verdict"] == "host_review_required"
    assert result["control"]["decision"] == "setup_incomplete"
    assert not any(result["control"]["permissions"].values())


def test_explicit_minimal_template_still_works(tmp_path):
    write(tmp_path, ".claude/settings.json")
    result = payload("init", "--workspace", str(tmp_path), "--minimal", "--write", "--json")
    assert result["manifest_status"] == "written"
    assert (tmp_path / "shipgate.yaml").is_file()


def test_old_discovery_payload_cannot_prove_a_terminal_negative():
    from agents_shipgate.triggers import evaluate
    legacy = {"is_agent_project": False, "suggested_sources": [], "codex_plugin_candidates": [], "python_parse_truncated": False}
    result = evaluate(paths=["unknown.file"], detect_result=legacy)
    assert not result["stop_conditions_evaluated"]
    assert not result["stop_conditions_fired"]


@pytest.mark.parametrize("name", ["docs/README.md", "LICENSE", ".mcp.json"])
def test_a_link_to_a_file_conceals_nothing_and_keeps_the_negative(tmp_path, zero, name):
    """A link with no descendants must not withhold the product-wide negative.

    `docs/README.md -> ../README.md` is ordinary in a repository that is not a
    Shipgate target at all, and counting it as concealment turned that
    settled negative into a human stop about a file (PR #614 review).
    """
    write(tmp_path, "README.md", "# x")
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "src/a.py", "x = 1\n")
    link = tmp_path / name
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(Path("..") / "README.md" if "/" in name else Path("README.md"))
    except OSError:
        pytest.skip("symlinks unavailable")

    canonical = detect_workspace(tmp_path)
    assert canonical.host_discovery_incomplete_paths == []
    assert zero.detect(tmp_path)["host_discovery_incomplete_paths"] == []
    result = payload("detect", "--workspace", str(tmp_path), "--json")
    assert result["host_discovery_incomplete_paths"] == []
    if name == ".mcp.json":
        # It is still a recognized configuration name, and still a candidate.
        assert [c["path"] for c in result["host_boundary_candidates"]] == [".mcp.json"]
        assert result["host_boundary_candidates"][0]["file_type"] == "symlink"
    else:
        assert result["host_boundary_candidates"] == []
        assert result["control"]["decision"] == "setup_not_applicable"
        assert [d["id"] for d in result["diagnostics"]] == ["SHIP-DIAG-NON-AGENT-LIBRARY"]
        assert result["surface_exclusions"]["entries"] == []


def test_a_link_to_a_directory_still_withholds_the_negative(tmp_path, zero):
    (tmp_path / "real").mkdir()
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    try:
        (tmp_path / "docs").symlink_to(tmp_path / "real", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    assert detect_workspace(tmp_path).host_discovery_incomplete_paths == ["docs"]
    assert zero.detect(tmp_path)["host_discovery_incomplete_paths"] == ["docs"]


# Every path shape the boundary registry can match, and whether host discovery
# treats it as a configuration candidate. `host_config_adapters_for_path`
# answers this with a suffix gate plus a hard-coded instruction-prefix denylist
# sitting beside an otherwise registry-driven lookup, so a *new* adapter glob
# under an instruction directory would silently widen the candidate set with
# nothing failing. Pinning the decision per registry entry makes that a failing
# test and a deliberate choice instead (PR #614 review).
REGISTRY_ELIGIBILITY: dict[str, bool] = {
    ".codex/config.toml": True,
    ".codex/hooks.json": True,
    ".codex/requirements.toml": True,
    "**/.codex/config.toml": True,
    "**/.codex/hooks.json": True,
    "**/.codex/requirements.toml": True,
    ".claude/settings.json": True,
    ".claude/settings.local.json": True,
    ".mcp.json": True,
    "**/.mcp.json": True,
    ".cursor/cli.json": True,
    ".cursor/mcp.json": True,
    ".vscode/mcp.json": True,
    # Instructions, commands and skills: prose and prompt surfaces the host
    # audit reads for a different question. Never host configuration.
    "CLAUDE.md": False,
    "**/CLAUDE.md": False,
    ".claude/commands/*": False,
    ".claude/commands/**": False,
    ".claude/skills/*/SKILL.md": False,
    ".claude/skills/**/SKILL.md": False,
    ".cursor/rules/*": False,
    ".cursor/rules/**": False,
    # Shared governance: this repository's own manifest, policies, baselines
    # and workflows. Governed surfaces, not a host's configuration.
    "AGENTS.md": False,
    "AGENTS.override.md": False,
    "**/AGENTS.md": False,
    "**/AGENTS.override.md": False,
    "shipgate.yaml": False,
    ".shipgate/agent-contract.json": False,
    "policies/agent-boundary.shipgate.yaml": False,
    "policies/codex-boundary.shipgate.yaml": False,
    "policies/host-boundary.shipgate.yaml": False,
    "policies/*.shipgate.yaml": False,
    ".agents/skills/*/SKILL.md": False,
    ".agents/skills/**/SKILL.md": False,
    ".github/workflows/*.yml": False,
    ".github/workflows/*.yaml": False,
    "**/.github/workflows/*.yml": False,
    "**/.github/workflows/*.yaml": False,
    ".agents-shipgate/baseline*.json": False,
    ".agents-shipgate/*waiver*.json": False,
    ".agents-shipgate/state*.json": False,
}


def test_every_registry_surface_has_a_pinned_host_config_decision():
    """A new adapter entry must force a decision, not inherit one silently."""
    registry = {
        path
        for adapter in BOUNDARY_ADAPTERS
        for path in (*adapter.exact_paths, *adapter.globs)
    }
    assert registry == set(REGISTRY_ELIGIBILITY), (
        "the boundary registry changed; decide whether each new entry is host "
        "configuration and pin it here:\n"
        f"  only in registry: {sorted(registry - set(REGISTRY_ELIGIBILITY))}\n"
        f"  only pinned here: {sorted(set(REGISTRY_ELIGIBILITY) - registry)}"
    )
    for pattern, eligible in sorted(REGISTRY_ELIGIBILITY.items()):
        # A concrete path the pattern matches, since the predicate reads paths.
        # Every expansion ends in `.json` so a `False` here has to come from the
        # instruction/governance rules rather than from the suffix gate — the
        # suffix would make the whole table pass for the wrong reason.
        concrete = (
            pattern.replace("**/", "nested/")
            .replace("/**", "/nested/example.json")
            .replace("*", "example.json")
        )
        assert bool(host_config_adapters_for_path(concrete)) is eligible, (
            f"{pattern!r} (as {concrete!r}) is "
            f"{'not ' if eligible else ''}treated as host configuration"
        )

    # The two exclusions that are neither a suffix nor the `shared` adapter,
    # spelled directly: these are the ones a new adapter glob would slip past.
    for instruction in (".claude/commands/hook.json", ".cursor/rules/rule.json"):
        assert not host_config_adapters_for_path(instruction), instruction
        assert boundary_adapters_for_path(instruction), (
            f"{instruction} must still be a boundary surface, or this proves nothing"
        )
