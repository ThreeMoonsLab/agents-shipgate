from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
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


def test_install_hooks_rejects_head_without_base(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "install-hooks",
            "--workspace",
            str(tmp_path),
            "--base",
            "",
            "--head",
            "HEAD",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "--head requires --base" in result.output


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


def test_generated_post_tool_hook_evaluates_relevance_without_manifest_force_run(
    tmp_path: Path,
) -> None:
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
    # A configured repository would force every PR through Shipgate, but the
    # edit-time hook deliberately passes manifest_present=false so an ordinary
    # docs edit remains quiet rather than becoming a force-run nudge.
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
    _init_repo(tmp_path)
    agent = tmp_path / "agent.py"
    agent.write_text(
        "from agents import function_tool\n\n@function_tool\ndef lookup() -> str:\n"
        "    return ''\n",
        encoding="utf-8",
    )
    hook_namespace = _rendered_hook_namespace(tmp_path)
    git_diff_for_paths = hook_namespace["_git_diff_for_paths"]
    assert "@function_tool" in git_diff_for_paths(tmp_path, ["agent.py"])
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
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "Agents Shipgate trigger matched" in context


def test_post_tool_hook_treats_custom_manifest_as_relevant_without_catalog_match(
    tmp_path: Path,
    capsys,
) -> None:
    namespace = _rendered_hook_namespace(tmp_path)
    custom = tmp_path / "config" / "release.gate"
    custom.parent.mkdir()
    custom.write_text("version: '0.1'\n", encoding="utf-8")
    namespace["_git_diff_for_paths"] = lambda *_args, **_kwargs: "diff"
    namespace["_run_trigger_for_paths"] = lambda *_args, **_kwargs: {
        "should_run": False
    }
    args = SimpleNamespace(
        config="config/release.gate",
        base="HEAD",
        head="HEAD",
        ci_mode="advisory",
    )

    result = namespace["_trigger"](
        {"tool_input": {"file_path": str(custom)}},
        tmp_path,
        args,
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "configured protected surface changed" in context.lower()
    assert "--head" not in context


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
    assert "verify could not start" in payload["systemMessage"]


def test_generated_stop_hook_warns_when_clean_repo_base_is_unavailable(
    tmp_path: Path,
) -> None:
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
    payload = json.loads(result.stdout)
    assert "decision" not in payload
    assert "configured base ref is unavailable" in payload["systemMessage"]
    assert "Fetch the base ref" in payload["systemMessage"]
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


def test_generated_stop_hook_omits_configured_head_for_worktree_snapshot(
    tmp_path: Path,
) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="HEAD",
        head="HEAD",
        ci_mode="advisory",
    )
    _init_repo(tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "refund.md").write_text(
        "require approval\n",
        encoding="utf-8",
    )
    log = tmp_path.parent / f"{tmp_path.name}-cli.log"
    fake_cli = _fake_shipgate_cli(tmp_path)
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} {fake_cli} {log}"
    env["AGENTS_SHIPGATE_VERIFY_BASE"] = "HEAD"
    env["AGENTS_SHIPGATE_VERIFY_HEAD"] = "HEAD"

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
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    verify_args = next(entry for entry in entries if entry[0] == "verify")
    assert verify_args[verify_args.index("--base") + 1] == "HEAD"
    assert "--head" not in verify_args


def test_generated_stop_hook_warns_on_index_hidden_worktree_path(
    tmp_path: Path,
) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="HEAD",
        head="",
        ci_mode="advisory",
    )
    hidden = tmp_path / "inventory.surface"
    hidden.write_text("safe\n", encoding="utf-8")
    _init_repo(tmp_path)
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", hidden.name],
        cwd=tmp_path,
        check=True,
    )
    hidden.write_text("expanded authority\n", encoding="utf-8")
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
    payload = json.loads(result.stdout)
    assert "could not collect a bounded, static worktree snapshot" in payload[
        "systemMessage"
    ]
    assert not log.exists()


def test_generated_stop_hook_binds_ignored_custom_manifest(
    tmp_path: Path,
) -> None:
    render_or_install_hooks(
        workspace=tmp_path,
        target="claude-code",
        write=True,
        config=Path("config/release.gate"),
        base="HEAD",
        head="",
        ci_mode="advisory",
    )
    _init_repo(tmp_path)
    with (tmp_path / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write("config/release.gate\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "ignore custom gate"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    custom = tmp_path / "config" / "release.gate"
    custom.parent.mkdir()
    custom.write_text("version: '0.1'\n", encoding="utf-8")
    log = tmp_path.parent / f"{tmp_path.name}-cli.log"
    fake_cli = _fake_shipgate_cli(tmp_path)
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} {fake_cli} {log}"

    result = subprocess.run(
        [
            sys.executable,
            str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH),
            "verify",
            "--config",
            "config/release.gate",
            "--base",
            "HEAD",
        ],
        input=json.dumps({"cwd": str(tmp_path)}),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(entry[0] == "verify" for entry in entries)


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
                "allowed_next_commands": ["agents-shipgate verify --json"],
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


def test_stop_hook_says_publishing_is_still_authorized_on_review_publishable(
    tmp_path: Path,
) -> None:
    # Contract v20: a human gates the merge, not the pull request. The turn
    # still ends — the agent has no Shipgate work left — but the notice must
    # not read as "stop everything", or the workflow deadlocks on a change
    # that was never published for the human to look at.
    _stop_hook_workspace(tmp_path)
    payload = json.dumps(
        {
            "release_decision": {
                "decision": "review_required",
                "blockers": [],
                "review_items": [{}],
            },
            "control": {
                "state": "review_publishable",
                "reason": "capability change requires a reviewer",
                "must_stop": False,
                "allowed_next_commands": ["agents-shipgate verify --json"],
                "permissions": {
                    "edit": True,
                    "commit": True,
                    "push": True,
                    "update_pr": True,
                    "merge": False,
                    "report_complete": False,
                },
                "human_review": {
                    "required": True,
                    "why": "a reviewer must approve the new tool authority",
                },
            },
        }
    )
    result = _run_stop_hook(tmp_path, verify_payload=payload)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert "decision" not in out
    message = out["systemMessage"]
    assert "A human must review" in message
    assert "commit, push, and update the pull request" in message
    assert "may not merge it or report the task complete" in message
    assert "agents-shipgate verify --json" in message


@pytest.mark.parametrize(
    "permissions",
    [
        None,
        {"edit": True, "commit": True, "push": True, "update_pr": True,
         "merge": True, "report_complete": False},
        {"edit": False, "commit": False, "push": False, "update_pr": False,
         "merge": False, "report_complete": False},
    ],
)
def test_stop_hook_never_announces_publication_off_the_state_tag(
    tmp_path: Path, permissions: dict[str, bool] | None
) -> None:
    """This is a raw-JSON consumer; the tag is not the grant.

    A malformed payload can carry `review_publishable` with no vector, or one
    that grants merge. Announcing publication off the state would hand out
    authority the producer never wrote.
    """

    _stop_hook_workspace(tmp_path)
    control: dict[str, object] = {
        "state": "review_publishable",
        "reason": "capability change requires a reviewer",
        "must_stop": False,
        "allowed_next_commands": ["agents-shipgate verify --json"],
        "human_review": {"required": True, "why": "review"},
    }
    if permissions is not None:
        control["permissions"] = permissions
    payload = json.dumps(
        {
            "release_decision": {"decision": "review_required", "blockers": [], "review_items": [{}]},
            "control": control,
        }
    )
    result = _run_stop_hook(tmp_path, verify_payload=payload)
    assert result.returncode == 0, result.stderr
    message = json.loads(result.stdout)["systemMessage"]
    assert "without the exact publish-only permission vector" in message
    assert "commit, push, and update the pull request" not in message
    # A malformed result must not be cached as a verified tree state.
    rerun = _run_stop_hook(tmp_path, verify_payload=payload)
    assert json.loads(rerun.stdout)["systemMessage"] == message


def test_stop_hook_surfaces_fetch_base_without_inventing_a_command(
    tmp_path: Path,
) -> None:
    _stop_hook_workspace(tmp_path)
    payload = json.dumps(
        {
            "release_decision": {
                "decision": "unknown",
                "blockers": [],
                "review_items": [],
            },
            "control": {
                "state": "agent_action_required",
                "reason": "base ref is missing",
                "allowed_next_commands": [],
                "next_action": {
                    "kind": "fetch_base",
                    "expects": "origin/main",
                    "why": "Make the base ref available locally.",
                },
            },
        }
    )

    result = _run_stop_hook(tmp_path, verify_payload=payload)

    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["decision"] == "block"
    assert "origin/main" in out["reason"]
    assert "No executable command was authorized" in out["reason"]
    assert "agents-shipgate verify" not in out["reason"]


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
    assert "configured manifest 'shipgate.yaml' does not exist" in out["systemMessage"]
    assert "verify --preview" in out["systemMessage"]
    log = tmp_path.parent / f"{tmp_path.name}-cli.log"
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0][0] == "trigger"
    assert "--no-manifest-present" in entries[0]


def _pretooluse_out(
    tmp_path: Path,
    file_path: str,
    *,
    session_id: str = "S1",
    permission_mode: str = "default",
) -> str:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    payload = {
        "session_id": session_id,
        "permission_mode": permission_mode,
        "tool_input": {"file_path": file_path},
    }
    return subprocess.run(
        [sys.executable, str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH), "pretooluse"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    ).stdout


def _posttooluse(
    tmp_path: Path,
    file_path: str,
    *,
    session_id: str = "S1",
    permission_mode: str = "default",
) -> None:
    log = tmp_path.parent / f"{tmp_path.name}-cli.log"
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} {_fake_shipgate_cli(tmp_path)} {log}"
    payload = {
        "session_id": session_id,
        "permission_mode": permission_mode,
        "tool_input": {"file_path": file_path},
    }
    subprocess.run(
        [sys.executable, str(tmp_path / HOOK_SCRIPT_RELATIVE_PATH), "trigger"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )


def test_pretooluse_stops_re_asking_for_an_already_allowed_file(tmp_path: Path) -> None:
    """One human decision per file per session, not one per edit."""

    _stop_hook_workspace(tmp_path)

    assert "permissionDecision" in _pretooluse_out(tmp_path, "CLAUDE.md")
    _posttooluse(tmp_path, "CLAUDE.md")
    assert _pretooluse_out(tmp_path, "CLAUDE.md") == ""

    # A different session never inherits the decision.
    assert "permissionDecision" in _pretooluse_out(
        tmp_path, "CLAUDE.md", session_id="S2"
    )
    # Nor does an unrelated protected file.
    assert "permissionDecision" in _pretooluse_out(tmp_path, "shipgate.yaml")


def test_auto_answering_permission_modes_are_not_recorded_as_approval(
    tmp_path: Path,
) -> None:
    """An edit nobody was asked about is not an approval."""

    _stop_hook_workspace(tmp_path)
    _posttooluse(tmp_path, "CLAUDE.md", permission_mode="bypassPermissions")
    assert "permissionDecision" in _pretooluse_out(tmp_path, "CLAUDE.md")

    # An absent mode is unknown, not permission to remember.
    _posttooluse(tmp_path, "CLAUDE.md", permission_mode="")
    assert "permissionDecision" in _pretooluse_out(tmp_path, "CLAUDE.md")


def test_accept_edits_mode_still_records_an_answered_prompt(tmp_path: Path) -> None:
    """acceptEdits auto-accepts ordinary edits, but an explicit hook ask still
    reaches the human — so a landed protected edit is an answered prompt."""

    _stop_hook_workspace(tmp_path)
    _posttooluse(tmp_path, "CLAUDE.md", permission_mode="acceptEdits")
    assert _pretooluse_out(tmp_path, "CLAUDE.md") == ""


def test_approval_memory_never_overrides_a_configured_deny(tmp_path: Path) -> None:
    """`deny` is an operator's hard block, not a prompt to be remembered."""

    _stop_hook_workspace(tmp_path)
    _posttooluse(tmp_path, "CLAUDE.md")
    assert _pretooluse_out(tmp_path, "CLAUDE.md") == ""

    env_backup = os.environ.get("AGENTS_SHIPGATE_PRETOOLUSE_DECISION")
    os.environ["AGENTS_SHIPGATE_PRETOOLUSE_DECISION"] = "deny"
    try:
        out = _pretooluse_out(tmp_path, "CLAUDE.md")
        assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        if env_backup is None:
            os.environ.pop("AGENTS_SHIPGATE_PRETOOLUSE_DECISION", None)
        else:
            os.environ["AGENTS_SHIPGATE_PRETOOLUSE_DECISION"] = env_backup


def test_disabled_boundary_does_not_seed_approval_memory(tmp_path: Path) -> None:
    """With the boundary disabled no request is made, so nothing was allowed."""

    _stop_hook_workspace(tmp_path)
    env_backup = os.environ.get("AGENTS_SHIPGATE_PRETOOLUSE_DECISION")
    os.environ["AGENTS_SHIPGATE_PRETOOLUSE_DECISION"] = "allow"
    try:
        _posttooluse(tmp_path, "CLAUDE.md")
    finally:
        if env_backup is None:
            os.environ.pop("AGENTS_SHIPGATE_PRETOOLUSE_DECISION", None)
        else:
            os.environ["AGENTS_SHIPGATE_PRETOOLUSE_DECISION"] = env_backup

    assert "permissionDecision" in _pretooluse_out(tmp_path, "CLAUDE.md")


def test_outside_workspace_path_cannot_authorize_a_repository_path(
    tmp_path: Path,
) -> None:
    """A same-basename file elsewhere must not carry an approval inward."""

    _stop_hook_workspace(tmp_path)
    outsider = tmp_path.parent / f"{tmp_path.name}-elsewhere"
    outsider.mkdir(parents=True, exist_ok=True)
    stray = outsider / "shipgate.yaml"
    stray.write_text("version: '0.1'\n", encoding="utf-8")

    _posttooluse(tmp_path, str(stray))
    assert "permissionDecision" in _pretooluse_out(
        tmp_path, str(tmp_path / "shipgate.yaml")
    )


def test_approval_memory_preserves_other_sessions(tmp_path: Path) -> None:
    _stop_hook_workspace(tmp_path)
    _posttooluse(tmp_path, "CLAUDE.md", session_id="A")
    _posttooluse(tmp_path, "shipgate.yaml", session_id="B")

    assert _pretooluse_out(tmp_path, "CLAUDE.md", session_id="A") == ""
    assert _pretooluse_out(tmp_path, "shipgate.yaml", session_id="B") == ""


def test_approval_memory_can_be_disabled(tmp_path: Path) -> None:
    _stop_hook_workspace(tmp_path)
    _posttooluse(tmp_path, "CLAUDE.md")
    env_backup = os.environ.get("AGENTS_SHIPGATE_APPROVAL_MEMORY")
    os.environ["AGENTS_SHIPGATE_APPROVAL_MEMORY"] = "off"
    try:
        assert "permissionDecision" in _pretooluse_out(tmp_path, "CLAUDE.md")
    finally:
        if env_backup is None:
            os.environ.pop("AGENTS_SHIPGATE_APPROVAL_MEMORY", None)
        else:
            os.environ["AGENTS_SHIPGATE_APPROVAL_MEMORY"] = env_backup


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
    # `init` always ensures this; without it the generated reports would show up
    # as untracked changes and perturb the hook's own input snapshot.
    (path / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")
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


def _rendered_hook_namespace(tmp_path: Path) -> dict[str, object]:
    script = _render_hook_script(tmp_path)
    namespace: dict[str, object] = {"__name__": "hook_under_test"}
    code = script.read_text(encoding="utf-8")
    exec(compile(code, str(script), "exec"), namespace)
    return namespace


def test_rendered_bounded_git_runner_fails_closed_on_output_overflow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    namespace = _rendered_hook_namespace(tmp_path)

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"overflow")
            self.killed = False

        def wait(self, timeout=None) -> int:
            return 0

        def kill(self) -> None:
            self.killed = True

    process = FakeProcess()
    subprocess_module = namespace["subprocess"]
    monkeypatch.setattr(subprocess_module, "Popen", lambda *args, **kwargs: process)

    run_git_bounded = namespace["_run_git_bounded"]
    assert run_git_bounded(tmp_path, ["status"], limit=4) is None
    assert process.killed is True


def test_rendered_bounded_git_runner_fails_closed_on_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    namespace = _rendered_hook_namespace(tmp_path)

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO()
            self.killed = False
            self.wait_calls = 0

        def wait(self, timeout=None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(cmd=["git"], timeout=timeout)
            return -9

        def kill(self) -> None:
            self.killed = True

    process = FakeProcess()
    subprocess_module = namespace["subprocess"]
    monkeypatch.setattr(subprocess_module, "Popen", lambda *args, **kwargs: process)

    run_git_bounded = namespace["_run_git_bounded"]
    assert run_git_bounded(tmp_path, ["status"], limit=1024) is None
    assert process.killed is True


def test_rendered_post_tool_diff_fails_closed_on_bounded_git_failure(
    tmp_path: Path,
) -> None:
    namespace = _rendered_hook_namespace(tmp_path)
    namespace["_is_git_repository"] = lambda _root: True
    namespace["_has_executable_worktree_filter"] = lambda _root: False
    namespace["_run_git_bounded"] = lambda *_args, **_kwargs: None

    git_diff_for_paths = namespace["_git_diff_for_paths"]
    assert git_diff_for_paths(tmp_path, ["agent.py"]) is None


def test_rendered_post_tool_diff_fails_closed_on_executable_filter(
    tmp_path: Path,
) -> None:
    namespace = _rendered_hook_namespace(tmp_path)
    namespace["_is_git_repository"] = lambda _root: True
    namespace["_has_executable_worktree_filter"] = lambda _root: True

    git_diff_for_paths = namespace["_git_diff_for_paths"]
    assert git_diff_for_paths(tmp_path, ["agent.py"]) is None


def test_rendered_untracked_diff_enforces_an_aggregate_content_budget(
    tmp_path: Path,
) -> None:
    namespace = _rendered_hook_namespace(tmp_path)
    namespace["GIT_DIFF_OUTPUT_LIMIT_BYTES"] = 64
    namespace["_run_git_bounded"] = lambda *_args, **_kwargs: b""
    metadata = SimpleNamespace(st_size=8, st_mtime_ns=1)
    namespace["_read_untracked_file"] = (
        lambda _root, _path: (b"12345678", metadata)
    )

    untracked_content = namespace["_untracked_content_for_paths"]
    assert untracked_content(
        tmp_path,
        ["one.py", "two.py", "three.py"],
    ) is None


def test_rendered_git_path_arguments_are_literal_pathspecs(tmp_path: Path) -> None:
    namespace = _rendered_hook_namespace(tmp_path)
    calls: list[list[str]] = []

    def fake_run(_root, args, *, limit):
        calls.append(args)
        if "ls-files" in args:
            return b":(exclude)**\0shipgate.yaml\0"
        return b""

    namespace["_is_git_repository"] = lambda _root: True
    namespace["_ref_exists"] = lambda _root, _ref: True
    namespace["_has_executable_worktree_filter"] = lambda _root: False
    namespace["_run_git_bounded"] = fake_run

    diff_for_paths = namespace["_git_diff_for_paths"]
    assert diff_for_paths(tmp_path, [":(exclude)**", "shipgate.yaml"]) == ""
    flattened = [argument for call in calls for argument in call]
    assert ":(top,literal):(exclude)**" in flattened
    assert ":(exclude)**" not in flattened


def test_rendered_untracked_binary_marker_binds_content_digest(
    tmp_path: Path,
) -> None:
    namespace = _rendered_hook_namespace(tmp_path)
    namespace["_run_git_bounded"] = lambda *_args, **_kwargs: b""
    metadata = SimpleNamespace(st_size=4)
    untracked_content = namespace["_untracked_content_for_paths"]

    namespace["_read_untracked_file"] = lambda _root, _path: (b"a\0aa", metadata)
    first = untracked_content(tmp_path, ["agent.bin"])
    namespace["_read_untracked_file"] = lambda _root, _path: (b"b\0bb", metadata)
    second = untracked_content(tmp_path, ["agent.bin"])

    assert first is not None
    assert second is not None
    assert first != second
    assert "sha256=" in first


def test_rendered_untracked_oversized_file_makes_snapshot_unavailable(
    tmp_path: Path,
) -> None:
    namespace = _rendered_hook_namespace(tmp_path)
    namespace["_run_git_bounded"] = lambda *_args, **_kwargs: b""
    limit = namespace["UNTRACKED_DIFF_CONTENT_LIMIT_BYTES"]
    metadata = SimpleNamespace(st_size=limit + 1)
    namespace["_read_untracked_file"] = lambda _root, _path: (b"", metadata)

    untracked_content = namespace["_untracked_content_for_paths"]
    assert untracked_content(tmp_path, ["large-agent.py"]) is None


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


def test_rendered_alias_inspector_rejects_nonexact_unicode_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    namespace = _rendered_hook_namespace(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    stored = config_dir / "café.gate"
    stored.write_text("version: '0.1'\n", encoding="utf-8")
    requested = config_dir / "cafe\u0301.gate"
    original_lstat = Path.lstat

    def aliasing_lstat(path: Path):
        if path == requested:
            return original_lstat(stored)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", aliasing_lstat)
    unsafe_alias_kind = namespace["_unsafe_alias_kind"]
    assert (
        unsafe_alias_kind(tmp_path, "config/cafe\u0301.gate")
        == "aliased-path"
    )


def test_rendered_script_glob_matcher_matches_canonical_globbing(
    tmp_path: Path,
) -> None:
    """The hook script embeds a copy of core.globbing.glob_match; pin that
    the rendered copy classifies every trust-root pattern identically."""
    from agents_shipgate.checks.verify import TRUST_ROOT_SURFACES
    from agents_shipgate.core.globbing import glob_match

    namespace = _rendered_hook_namespace(tmp_path)
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
