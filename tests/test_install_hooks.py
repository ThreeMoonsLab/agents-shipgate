from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.install_hooks import (
    HOOK_SCRIPT_RELATIVE_PATH,
    SETTINGS_RELATIVE_PATH,
    render_or_install_hooks,
)
from agents_shipgate.cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def test_install_hooks_dry_run_does_not_write(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "install-hooks",
            "--workspace",
            str(tmp_path),
            "--target",
            "claude-code",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["settings_status"] == "would_write"
    assert payload["script_status"] == "would_write"
    assert payload["hooks"] == [
        {
            "event": "PreToolUse",
            "matcher": "Edit|Write|MultiEdit",
            "purpose": (
                "route edits to protected trust-root surfaces to the "
                "human for permission before they happen"
            ),
        },
        {
            "event": "PostToolUse",
            "matcher": "Edit|Write|MultiEdit",
            "purpose": "cheap trigger check after file-editing tools",
        },
        {
            "event": "Stop",
            "purpose": "full verify at relevant completion boundaries",
        },
    ]
    assert not (tmp_path / SETTINGS_RELATIVE_PATH).exists()
    assert not (tmp_path / HOOK_SCRIPT_RELATIVE_PATH).exists()


def test_install_hooks_write_merges_and_is_idempotent(tmp_path: Path) -> None:
    settings = tmp_path / SETTINGS_RELATIVE_PATH
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo existing",
                                }
                            ],
                        }
                    ]
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    first = render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    assert first.settings_status == "written"
    assert first.script_status == "written"

    data = json.loads(settings.read_text(encoding="utf-8"))
    post_groups = data["hooks"]["PostToolUse"]
    assert any(group["matcher"] == "Bash" for group in post_groups)
    shipgate_groups = [
        group
        for group in post_groups
        if group["hooks"][0].get("args", [None, None])[1] == "trigger"
    ]
    assert len(shipgate_groups) == 1
    assert data["hooks"]["Stop"][0]["hooks"][0]["args"][1] == "verify"
    assert "--head" not in data["hooks"]["Stop"][0]["hooks"][0]["args"]
    assert "matcher" not in data["hooks"]["Stop"][0]
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "python3"
    assert (tmp_path / HOOK_SCRIPT_RELATIVE_PATH).is_file()

    second = render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    assert second.settings_status == "unchanged"
    assert second.script_status == "unchanged"


def test_install_hooks_rejects_unknown_target(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["install-hooks", "--workspace", str(tmp_path), "--target", "codex", "--json"],
    )

    assert result.exit_code == 2
    assert "Unsupported hook target" in result.output


def test_generated_post_tool_hook_emits_trigger_context(tmp_path: Path) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    (tmp_path / "shipgate.yaml").write_text("version: '0.1'\n", encoding="utf-8")
    event = {
        "hook_event_name": "PostToolUse",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "shipgate.yaml")},
    }
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} -m agents_shipgate"
    env["PYTHONPATH"] = str(REPO_ROOT / "src")

    result = subprocess.run(
        [
            sys.executable,
            str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH),
            "trigger",
        ],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "Agents Shipgate trigger matched" in context
    assert "agents-shipgate verify" in context
    assert "Do not bypass the verifier" in context


def test_generated_post_tool_hook_ignores_irrelevant_docs_edit(tmp_path: Path) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    (tmp_path / "shipgate.yaml").write_text("version: '0.1'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("docs\n", encoding="utf-8")
    event = {
        "hook_event_name": "PostToolUse",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_path": str(tmp_path / "README.md")},
    }
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} -m agents_shipgate"
    env["PYTHONPATH"] = str(REPO_ROOT / "src")

    result = subprocess.run(
        [
            sys.executable,
            str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH),
            "trigger",
        ],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_generated_post_tool_hook_matches_untracked_diff_tokens(tmp_path: Path) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    (tmp_path / "shipgate.yaml").write_text("version: '0.1'\n", encoding="utf-8")
    agent = tmp_path / "agent.py"
    agent.write_text(
        "from agents import function_tool\n\n@function_tool\ndef lookup() -> str:\n"
        "    return ''\n",
        encoding="utf-8",
    )
    event = {
        "hook_event_name": "PostToolUse",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_path": str(agent)},
    }
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} -m agents_shipgate"
    env["PYTHONPATH"] = str(REPO_ROOT / "src")

    result = subprocess.run(
        [
            sys.executable,
            str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH),
            "trigger",
        ],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "Agents Shipgate trigger matched" in (
        payload["hookSpecificOutput"]["additionalContext"]
    )


def test_generated_stop_hook_advisory_uses_system_message(tmp_path: Path) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    _init_repo(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "refund.md").write_text("require approval\n", encoding="utf-8")
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = str(tmp_path / "missing-shipgate")

    result = subprocess.run(
        [sys.executable, str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH), "verify"],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "systemMessage" in payload
    assert "hookSpecificOutput" not in payload
    assert "could not evaluate the local trigger" in payload["systemMessage"]


def test_generated_stop_hook_skips_clean_opted_in_repo(tmp_path: Path) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    _init_repo(tmp_path)
    log = tmp_path.parent / f"{tmp_path.name}-cli.log"
    fake_cli = _fake_shipgate_cli(tmp_path)
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} {fake_cli} {log}"

    result = subprocess.run(
        [sys.executable, str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH), "verify"],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not log.exists()


def test_generated_stop_hook_verifies_worktree_once_without_head(tmp_path: Path) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    _init_repo(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "refund.md").write_text("require approval\n", encoding="utf-8")
    log = tmp_path.parent / f"{tmp_path.name}-cli.log"
    fake_cli = _fake_shipgate_cli(tmp_path)
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} {fake_cli} {log}"

    first = subprocess.run(
        [sys.executable, str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH), "verify"],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH), "verify"],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    trigger_entries = [entry for entry in entries if entry[0] == "trigger"]
    verify_entries = [entry for entry in entries if entry[0] == "verify"]
    assert len(trigger_entries) == 2
    assert len(verify_entries) == 1
    verify_args = verify_entries[0]
    assert "--head" not in verify_args
    assert "--base" not in verify_args
    assert "--no-manifest-present" in trigger_entries[0]


def _run_stop_hook(
    tmp_path: Path,
    *,
    verify_payload: str | None,
    stop_hook_active: bool = False,
) -> subprocess.CompletedProcess[str]:
    log = tmp_path.parent / f"{tmp_path.name}-cli.log"
    fake_cli = _fake_shipgate_cli(tmp_path)
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} {fake_cli} {log}"
    if verify_payload is not None:
        env["FAKE_VERIFY_PAYLOAD"] = verify_payload
    payload: dict[str, object] = {"cwd": str(tmp_path)}
    if stop_hook_active:
        payload["stop_hook_active"] = True
    return subprocess.run(
        [sys.executable, str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH), "verify"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )


def _stop_hook_workspace(tmp_path: Path) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    _init_repo(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "refund.md").write_text("require approval\n", encoding="utf-8")


def test_stop_hook_blocks_only_for_agent_action_required(tmp_path: Path) -> None:
    _stop_hook_workspace(tmp_path)
    payload = json.dumps(
        {
            "release_decision": {"decision": "review_required", "blockers": [], "review_items": [{}]},
            "control": {
                "state": "agent_action_required",
                "reason": "verify pending",
                "next_action": {"kind": "verify", "command": "agents-shipgate verify --json"},
            },
        }
    )
    result = _run_stop_hook(tmp_path, verify_payload=payload)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["decision"] == "block"
    assert "agents-shipgate verify --json" in out["reason"]

    # The re-entry guard still prevents a forced-continuation loop.
    rerun = _run_stop_hook(tmp_path, verify_payload=payload, stop_hook_active=True)
    assert rerun.returncode == 0, rerun.stderr
    assert rerun.stdout == ""


def test_stop_hook_hands_off_instead_of_blocking_on_human_review(tmp_path: Path) -> None:
    # ``must_stop=true`` means the turn must be allowed to end for a human —
    # a Stop-hook block would force the agent to keep working, the opposite.
    _stop_hook_workspace(tmp_path)
    payload = json.dumps(
        {
            "release_decision": {
                "decision": "blocked",
                "blockers": [{}],
                "review_items": [],
            },
            "control": {
                "state": "human_review_required",
                "reason": "capability change requires approval evidence",
                "stop_reason": "capability change requires approval evidence",
            },
        }
    )
    result = _run_stop_hook(tmp_path, verify_payload=payload)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert "decision" not in out
    assert "A human must review" in out["systemMessage"]
    assert "decision=blocked" in out["systemMessage"]

    # One handoff notice per tree state: the signature cache silences repeats.
    rerun = _run_stop_hook(tmp_path, verify_payload=payload)
    assert rerun.returncode == 0, rerun.stderr
    assert rerun.stdout == ""


def test_stop_hook_warns_and_never_caches_unparseable_verifier_output(
    tmp_path: Path,
) -> None:
    _stop_hook_workspace(tmp_path)
    result = _run_stop_hook(tmp_path, verify_payload="not-json{")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert "decision" not in out
    assert "could not parse" in out["systemMessage"]
    assert "Do not treat this as a passing verdict" in out["systemMessage"]

    # Unparseable output must not be cached: the warning repeats.
    rerun = _run_stop_hook(tmp_path, verify_payload="not-json{")
    assert rerun.returncode == 0, rerun.stderr
    assert "could not parse" in json.loads(rerun.stdout)["systemMessage"]


def test_stop_hook_warns_on_unrecognized_control_state(tmp_path: Path) -> None:
    _stop_hook_workspace(tmp_path)
    payload = json.dumps(
        {
            "release_decision": {"decision": "passed", "blockers": [], "review_items": []},
            "control": {"state": "surprise_state", "reason": "future contract"},
        }
    )
    result = _run_stop_hook(tmp_path, verify_payload=payload)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert "decision" not in out
    assert "unrecognized control state" in out["systemMessage"]
    assert "requiring human review" in out["systemMessage"]


def test_stop_hook_cold_start_advises_instead_of_blocking(tmp_path: Path) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "refund.md").write_text("require approval\n", encoding="utf-8")
    result = _run_stop_hook(tmp_path, verify_payload=None)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert "decision" not in out
    assert "no shipgate.yaml exists" in out["systemMessage"]
    assert "verify --preview" in out["systemMessage"]


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "shipgate@example.test"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Shipgate Test"],
        cwd=path,
        check=True,
    )
    (path / "shipgate.yaml").write_text(
        "version: '0.1'\nagent:\n  name: test\n  declared_purpose: test\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _fake_shipgate_cli(path: Path) -> Path:
    script = path.parent / f"{path.name}-fake_shipgate.py"
    script.write_text(
        """
from __future__ import annotations

import json
import sys
from pathlib import Path

log = Path(sys.argv[1])
args = sys.argv[2:]
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")

if args and args[0] == "trigger":
    print(json.dumps({"should_run": True, "rationale": "test trigger matched"}))
    raise SystemExit(0)
if args and args[0] == "verify":
    import os
    override = os.environ.get("FAKE_VERIFY_PAYLOAD")
    if override is not None:
        sys.stdout.write(override)
        raise SystemExit(0)
    print(json.dumps({
        "release_decision": {
            "decision": "passed",
            "blockers": [],
            "review_items": [],
        },
        "base_status": "not_requested",
        "control": {"state": "complete", "reason": "test pass"},
    }))
    raise SystemExit(0)
raise SystemExit(2)
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _render_hook_script(tmp_path: Path) -> Path:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    return tmp_path / HOOK_SCRIPT_RELATIVE_PATH


def _run_pretooluse(tmp_path: Path, file_path: str, *, env_extra=None) -> str:
    script = _render_hook_script(tmp_path)
    event = {
        "hook_event_name": "PreToolUse",
        "cwd": str(tmp_path),
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path},
    }
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env.update(env_extra or {})
    result = subprocess.run(
        [sys.executable, str(script), "pretooluse"],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_pretooluse_hook_asks_for_protected_surface(tmp_path: Path) -> None:
    out = _run_pretooluse(tmp_path, str(tmp_path / "shipgate.yaml"))
    payload = json.loads(out)
    output = payload["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "ask"
    assert "shipgate.yaml" in output["permissionDecisionReason"]
    assert "never weaken" in output["permissionDecisionReason"]


def test_pretooluse_hook_silent_for_normal_files(tmp_path: Path) -> None:
    out = _run_pretooluse(tmp_path, str(tmp_path / "src" / "app.py"))
    assert out == ""


def test_pretooluse_hook_deny_mode_via_env(tmp_path: Path) -> None:
    out = _run_pretooluse(
        tmp_path,
        str(tmp_path / ".github" / "workflows" / "agents-shipgate.yml"),
        env_extra={"AGENTS_SHIPGATE_PRETOOLUSE_DECISION": "deny"},
    )
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pretooluse_hook_allow_mode_disables(tmp_path: Path) -> None:
    out = _run_pretooluse(
        tmp_path,
        str(tmp_path / "shipgate.yaml"),
        env_extra={"AGENTS_SHIPGATE_PRETOOLUSE_DECISION": "allow"},
    )
    assert out == ""


def test_rendered_script_glob_matcher_matches_canonical_globbing(
    tmp_path: Path,
) -> None:
    """The hook script embeds a copy of core.globbing.glob_match; pin that
    the rendered copy classifies every trust-root pattern identically."""
    from agents_shipgate.checks.verify import TRUST_ROOT_SURFACES
    from agents_shipgate.core.globbing import glob_match

    script = _render_hook_script(tmp_path)
    namespace: dict = {}
    # Extract just the rendered module constants/functions we need by
    # executing the script with a stubbed __name__ so main() doesn't run.
    code = script.read_text(encoding="utf-8")
    exec(compile(code, str(script), "exec"), {"__name__": "hook_under_test"}, namespace)
    hook_match = namespace["_glob_match"]
    surfaces = namespace["PROTECTED_SURFACES"]

    assert [tuple(item) for item in surfaces] == list(TRUST_ROOT_SURFACES)

    probes = [
        "shipgate.yaml",
        "nested/dir/shipgate.yaml",
        ".github/workflows/agents-shipgate.yml",
        "policies/org-release.yaml",
        ".agents-shipgate/baseline.json",
        "AGENTS.md",
        "src/app.py",
        "README.md",
        ".mcp.json",
        "docs/notes.txt",
    ]
    for _, pattern in surfaces:
        for probe in probes:
            assert hook_match(pattern, probe) == glob_match(pattern, probe), (
                pattern,
                probe,
            )
