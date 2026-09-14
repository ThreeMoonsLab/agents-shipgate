"""A benign coding session is not interrupted by repeated advisories (#661).

The hooks run the generated `.claude/hooks/agents-shipgate.py` as Claude Code
does, with a fake CLI whose trigger classifies paths the way the catalog does:
docs, tests and the README are classified, plain source is unclassified, and a
tool import matches a surface. The acceptance asks for zero redundant advisory
interrupts across 50 events of covered edits and a supported narrowing, while
unknown-relevance and unreadable input stay visible.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agents_shipgate.cli.install_hooks import HOOK_SCRIPT_RELATIVE_PATH, render_or_install_hooks

FAKE_CLI = r'''
import json, os, sys
from pathlib import Path

log = Path(sys.argv[1])
args = sys.argv[2:]
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")
if args and args[0] == "trigger":
    paths = Path(args[args.index("--changed-files") + 1]).read_text(encoding="utf-8").split()
    diff = Path(args[args.index("--diff") + 1]).read_text(encoding="utf-8")
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"paths": paths, "host_section": ".claude/settings.json" in diff}) + "\n")
    if any(path.endswith(".snap") for path in paths):
        result = {"should_run": None, "evaluation_status": "not_evaluated", "rationale": "The diff could not be read."}
    elif "import mcp" in diff or any(path.startswith("prompts/") for path in paths):
        result = {"should_run": True, "evaluation_status": "evaluated", "matched_rules": [{"id": "TRIGGER-TEST-SURFACE"}], "rationale": "A tool surface changed."}
    elif any(path.startswith(("src/", "scripts/")) for path in paths):
        result = {"should_run": None, "evaluation_status": "unclassified", "rationale": "No rule classified them."}
    else:
        result = {"should_run": False, "evaluation_status": "evaluated", "skip_reason": "skip_rule", "rationale": "Docs and tests only."}
    print(json.dumps(result))
    raise SystemExit(0)
if args and args[0] == "diff":
    sys.stdout.write(os.environ.get("FAKE_DIFF_PAYLOAD", json.dumps({"comparison_status": "comparable", "rows": []})))
    raise SystemExit(0)
raise SystemExit(2)
'''

NARROWING = {
    "comparison_status": "comparable",
    "rows": [
        {"subject": "claude-code .claude/settings.json", "before": "Bash(*)", "after": "—", "direction": "removed", "severity": "high", "why": "narrows", "expands": False},
        {"subject": "claude-code .claude/settings.json", "before": "—", "after": "Bash(git status)", "direction": "added", "severity": "low", "why": "narrows", "expands": False},
    ],
}
WIDENING = {
    "comparison_status": "comparable",
    "rows": [
        {"subject": "claude-code .claude/settings.json", "before": "—", "after": "WebFetch(*)", "direction": "added", "severity": "critical", "why": "matches every target", "expands": True},
    ],
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path, *, manifest: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    render_or_install_hooks(
        workspace=repo,
        target="claude-code",
        write=True,
        config=Path("shipgate.yaml"),
        base="origin/main",
        head="",
        ci_mode="advisory",
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "shipgate@example.test")
    _git(repo, "config", "user.name", "Shipgate Test")
    _set_allow(repo, ["Bash(*)", "Read(**)"])
    for relative, text in {
        "README.md": "# Project\n",
        "docs/guide.md": "# Guide\n",
        "tests/test_one.py": "def test_one():\n    assert True\n",
        "src/app.py": "def main():\n    return 0\n",
        "scripts/util.py": "def util():\n    return 1\n",
    }.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_text(text, encoding="utf-8")
    if manifest:
        (repo / "shipgate.yaml").write_text("version: '0.1'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (tmp_path / "fake_cli.py").write_text(FAKE_CLI, encoding="utf-8")
    return repo


def _set_allow(repo: Path, allow: list[str]) -> None:
    path = repo / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["permissions"] = {"allow": allow}
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def _append(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _hook(repo: Path, mode: str, *, edited: str | None = None, session: str | None = "s1", diff_payload: dict | None = None) -> str:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(repo)
    env["AGENTS_SHIPGATE_CLI"] = f"{sys.executable} {repo.parent / 'fake_cli.py'} {repo.parent / 'cli.log'}"
    for name in ("AGENTS_SHIPGATE_VERIFY_BASE", "AGENTS_SHIPGATE_VERIFY_HEAD"):
        env.pop(name, None)
    if diff_payload is not None:
        env["FAKE_DIFF_PAYLOAD"] = json.dumps(diff_payload)
    body: dict = {"cwd": str(repo)}
    if session is not None:
        body["session_id"] = session
    if mode == "trigger":
        body.update(hook_event_name="PostToolUse", tool_name="Edit", tool_input={"file_path": str(repo / edited)})
    else:
        body.update(hook_event_name="Stop", stop_hook_active=False)
    completed = subprocess.run(
        [sys.executable, str(repo / HOOK_SCRIPT_RELATIVE_PATH), mode],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        env=env,
        cwd=repo,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _log(repo: Path) -> list:
    path = repo.parent / "cli.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_fifty_event_benign_session_never_interrupts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    covered = ["docs/guide.md", "tests/test_one.py", "README.md"]
    outputs: list[str] = []
    for turn in range(1, 26):
        if turn == 7:
            _set_allow(repo, ["Bash(git status)", "Read(**)"])
            edited = ".claude/settings.json"
        else:
            edited = covered[(turn - 1) % len(covered)]
            _append(repo, edited, f"\n# turn {turn}\n")
        outputs.append(_hook(repo, "trigger", edited=edited, diff_payload=NARROWING))
        outputs.append(_hook(repo, "verify", diff_payload=NARROWING))
        if turn % 5 == 0:
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", f"turn {turn}")

    assert len(outputs) == 50
    assert [output for output in outputs if output] == []
    log = _log(repo)
    evaluated = [entry for entry in log if isinstance(entry, dict)]
    assert evaluated, "the covered edits were evaluated, not skipped"
    assert all(".claude/settings.json" not in entry["paths"] and not entry["host_section"] for entry in evaluated)
    assert any(isinstance(entry, list) and entry[0] == "diff" for entry in log), "the narrowing was compared"


def test_unknown_relevance_is_announced_once_per_path_in_a_session(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    _append(repo, "src/app.py", "\n\ndef two():\n    return 2\n")
    first = _hook(repo, "trigger", edited="src/app.py")
    assert "could not decide whether this diff is relevant" in first
    assert _hook(repo, "verify") == ""

    _append(repo, "src/app.py", "\n\ndef three():\n    return 3\n")
    assert _hook(repo, "trigger", edited="src/app.py") == ""
    _append(repo, "docs/guide.md", "more\n")
    assert _hook(repo, "trigger", edited="docs/guide.md") == ""
    assert _hook(repo, "verify") == ""

    _append(repo, "scripts/util.py", "\n\ndef four():\n    return 4\n")
    assert "could not decide" in _hook(repo, "trigger", edited="scripts/util.py")
    assert _hook(repo, "verify") == ""

    # A new session has not heard it, and without a session id nothing is remembered.
    assert "could not decide" in _hook(repo, "trigger", edited="src/app.py", session="s2")
    assert "could not decide" in _hook(repo, "verify", session="s3")
    assert "could not decide" in _hook(repo, "trigger", edited="src/app.py", session=None)
    assert "could not decide" in _hook(repo, "trigger", edited="src/app.py", session=None)


def test_a_path_no_hook_evaluated_is_announced_at_stop(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _append(repo, "docs/guide.md", "more\n")
    assert _hook(repo, "trigger", edited="docs/guide.md") == ""
    # Written by a shell command, so PostToolUse never saw it.
    _append(repo, "scripts/util.py", "\n\ndef shell():\n    return 5\n")

    assert "could not decide" in _hook(repo, "verify")
    assert _hook(repo, "verify") == ""


def test_a_new_verdict_or_unread_input_is_announced_again(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _append(repo, "src/app.py", "\n\ndef two():\n    return 2\n")
    assert "could not decide" in _hook(repo, "trigger", edited="src/app.py")

    _append(repo, "src/app.py", "\nimport mcp\n")
    assert "trigger matched" in _hook(repo, "trigger", edited="src/app.py")
    _append(repo, "src/app.py", "\n# still a tool module\n")
    assert _hook(repo, "trigger", edited="src/app.py") == ""

    _append(repo, "pkg/opaque.snap", "{}\n")
    assert "could not decide" in _hook(repo, "trigger", edited="pkg/opaque.snap")
    assert "could not decide" in _hook(repo, "trigger", edited="pkg/opaque.snap")


def test_host_configuration_beside_other_edits_is_compared_and_advised_in_one_message(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _set_allow(repo, ["Bash(*)", "Read(**)", "WebFetch(*)"])
    _append(repo, "src/app.py", "\n\ndef two():\n    return 2\n")

    message = json.loads(_hook(repo, "verify", session=None, diff_payload=WIDENING))["systemMessage"]

    assert "WebFetch(*)" in message
    assert "never a permission" in message
    assert "configured manifest 'shipgate.yaml' does not exist" in message
    evaluated = [entry for entry in _log(repo) if isinstance(entry, dict)]
    assert [entry["paths"] for entry in evaluated] == [["src/app.py"]]
    assert not evaluated[0]["host_section"]


def test_post_tool_hook_is_quiet_on_host_config_edits_with_a_manifest(tmp_path: Path) -> None:
    repo = _repo(tmp_path, manifest=True)
    _set_allow(repo, ["Bash(*)", "Read(**)", "WebFetch(*)"])

    assert _hook(repo, "trigger", edited=".claude/settings.json") == ""
    assert not any(isinstance(entry, list) and entry[0] == "trigger" for entry in _log(repo))
