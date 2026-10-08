"""#825: bounded mutable-source notes without new authority claims."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    _mcp_grants,
    _mcp_launch_source,
    compared_grant,
    diff_host_grants,
    host_grant_expansion_signals,
)
from agents_shipgate.core.mcp_launch_source import launch_source_pin

SHA = "a" * 40
DIGEST = "b" * 64
PIN_CASES = [
    ("npx", ["pkg@1.2.3"], "pinned"),
    ("npx", ["pkg@v1.2.3"], "pinned"),
    ("npx", ["-y", "@scope/pkg@1.2.3-rc.1"], "pinned"),
    ("npx", ["pkg"], "mutable"),
    ("npx", ["--yes", "@scope/pkg"], "mutable"),
    ("npx", ["pkg@latest"], "mutable"),
    ("npx", ["pkg@release"], "mutable"),
    ("npx", ["pkg@^1.2.3"], "mutable"),
    ("npx", ["pkg@1.2"], "mutable"),
    ("bunx", ["--bun", "pkg@1.2.3"], "pinned"),
    ("bunx", ["pkg@next"], "mutable"),
    ("pnpm", ["dlx", "pkg@1.2.3"], "pinned"),
    ("pnpm", ["dlx", "pkg"], "mutable"),
    ("uvx", ["pkg==1.2.3"], "pinned"),
    ("uvx", ["pkg"], "mutable"),
    ("uvx", ["pkg~=1.2"], "mutable"),
    ("uvx", ["pkg==1.*"], "mutable"),
    ("uvx", ["pkg@latest"], "mutable"),
    ("uvx", ["pkg@1.2.3"], "pinned"),
    ("uvx", ["pkg[extra]@1.2.3", "--flag"], "pinned"),
    ("uvx", ["--from", "pkg==1.2.3", "server"], "pinned"),
    ("pipx", ["run", "--spec", "pkg==1.2.3", "server"], "pinned"),
    ("pipx", ["run", "--no-cache", "--spec", "pkg", "server"], "mutable"),
    ("pipx", ["run", "--spec", "git+https://example.com/repo.git@" + SHA, "server"], "pinned"),
    ("pipx", ["run", "--spec", "git+https://example.com/repo.git@main", "server"], "mutable"),
    ("pipx", ["run", "--spec", "git+https://example.com/repo.git@v1.2.3", "server"], "mutable"),
    ("pipx", ["run", "--spec", "git+https://example.com/repo.git", "server"], "mutable"),
    ("docker", ["run", "-i", "--rm", "org/image@sha256:" + DIGEST], "pinned"),
    ("docker", ["run", "image@sha256:" + DIGEST], "pinned"),
    ("docker", ["run", "registry.example:5000/org/image:1.2@sha256:" + DIGEST], "pinned"),
    ("docker", ["run", "image"], "mutable"),
    ("docker", ["run", "org/image:latest"], "mutable"),
    ("docker", ["run", "org/image:1.2.3"], "mutable"),
    # GitHub's documented launch: a flag's value is skipped, never the source.
    ("docker", ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"], "mutable"),
    ("docker", ["run", "-i", "--rm", "--env", "LOG_LEVEL", "mcp/fetch"], "mutable"),
    ("docker", ["run", "-it", "mcp/fetch"], "mutable"),
    ("docker", ["run", "-ti", "--rm", "mcp/fetch"], "mutable"),
    ("docker", ["run", "--rm", "-i", "--pull=always", "mcp/fetch"], "mutable"),
    ("docker", ["run", "-e=A=1", "--network=host", "--platform", "linux/amd64", "image:1"], "mutable"),
    ("docker", ["run", "--name", "org/image:latest", "actual-image"], "mutable"),
    ("docker", ["run", "-i", "-v", "/tmp:/tmp", "--mount", "type=bind,src=/a,dst=/b", "-w", "/b", "-u", "1000", "-p", "80:80", "--env-file", ".env", "--entrypoint", "srv", "--volume=/c:/c", "org/fs@sha256:" + DIGEST], "pinned"),
]


@pytest.mark.parametrize("command,args,pin", PIN_CASES)
def test_documented_launch_source_forms(command, args, pin):
    actual = launch_source_pin(command, args)
    assert actual is not None and actual[0] == pin
    source = _mcp_launch_source({"command": command, "args": args})
    assert source is not None and source["pin"] == pin


@pytest.mark.parametrize("command,args", [
    ("./npx", ["pkg@latest"]), ("/usr/bin/npx", ["pkg@latest"]),
    ("sh", ["-c", "npx pkg@latest"]), ("node", ["./server.js"]),
    ("npx pkg@latest", []), ("wrapper", ["pkg@latest"]),
    ("npx", ["--unknown", "pkg@latest"]), ("npx", ["--registry", "pkg@latest"]),
    ("npx", ["--package", "pkg@latest", "different-command"]),
    ("uvx", ["--with", "pkg==1.2.3", "other"]),
    ("pipx", ["run", "--spec", "pkg"]), ("pipx", ["install", "pkg"]),
    ("pnpm", ["exec", "pkg"]), ("docker", ["exec", "image"]),
    ("docker", ["run", "--privileged", "image"]), ("docker", ["run", "-d", "image"]),
    ("docker", ["run", "-itd", "image"]), ("docker", ["run", "--rm=true", "image"]),
    ("docker", ["run", "--cap-add=NET_ADMIN", "image"]), ("docker", ["run", "-eFOO", "image"]),
    ("docker", ["run", "-i", "-e"]), ("docker", ["run", "--name", "image"]),
    ("docker", ["run", "image@sha256:abc"]),
    ("npx", ["$PACKAGE"]), ("npx", ["${PACKAGE}"]),
    ("npx", ["pkg@latest", "${TOKEN}"]), ("npx", ["`source`"]),
    ("npx", ["https://example.com/archive.tgz"]),
    ("uvx", ["pkg==not-a-version"]), ("uvx", ["pkg>=unknown"]),
    ("uvx", ["./pkg"]), ("npx", ["pkg\n@latest"]),
    ("uvx", ["pkg@main"]), ("uvx", ["pkg@>=1.0"]), ("uvx", ["pkg@1.2@latest"]),
    ("uvx", ["--from", "pkg@latest", "tool"]), ("pipx", ["run", "--spec", "pkg@1.2.3", "tool"]),
    ("npx", "pkg@latest"), ("npx", [None]), ("npx", []),
    ("npx", ["pkg@latest"] * 65), ("npx", ["x" * 2049]),
])
def test_unknown_dynamic_and_wrapper_forms_abstain(command, args):
    assert launch_source_pin(command, args) is None
    assert _mcp_launch_source({"command": command, "args": args}) is None


def grant(config):
    return _mcp_grants({"mcpServers": {"docs": config}}, host="claude-code", scope="repository", source=".mcp.json")[0]


@pytest.mark.parametrize("before,after,phrase", [
    (None, {"command": "npx", "args": ["pkg@latest"]}, "launch source is mutable (pkg@latest)"),
    ({"command": "npx", "args": ["pkg@1.2.3"]}, {"command": "npx", "args": ["pkg@latest"]}, "launch source moved from pinned (pkg@1.2.3) to mutable (pkg@latest)"),
    # An unversioned npx package names its resolution, not mutability (#933).
    (None, {"command": "npx", "args": ["pkg"]}, "package spec has no exact version; launch resolution not established"),
    ({"command": "npx", "args": ["pkg@1.2.3"]}, {"command": "npx", "args": ["pkg@1.2.4"]}, None),
    (None, {"url": "https://example.com/mcp"}, None),
    (None, {"command": "node", "args": ["./server.js"]}, None),
    ({"command": "npx", "args": ["pkg@latest"]}, None, None),
    (None, {"command": "docker", "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"], "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "x"}}, "launch source is mutable"),
    ({"command": "uvx", "args": ["pkg@1.2.3"]}, {"command": "uvx", "args": ["pkg@latest"]}, "launch source moved from pinned (pkg@1.2.3) to mutable (pkg@latest)"),
])
def test_note_preserves_the_existing_row_and_signals(before, after, phrase):
    old, new = grant(before) if before else None, grant(after) if after else None
    change = {"baseline": old, "current": new}
    payload = {"changes": [change], "expansion_signals": host_grant_expansion_signals([change])}
    (row,) = capability_diff_rows(payload)
    def strip(g):
        return {k: v for k, v in g.items() if k != "launch_source"} if g else None
    bare = {"baseline": strip(old), "current": strip(new)}
    (original,) = capability_diff_rows({"changes": [bare], "expansion_signals": host_grant_expansion_signals([bare])})
    assert {k: v for k, v in row.as_dict().items() if k != "why"} == {k: v for k, v in original.as_dict().items() if k != "why"}
    assert host_grant_expansion_signals([change]) == host_grant_expansion_signals([bare])
    assert compared_grant(new) == compared_grant(strip(new))
    assert compared_grant(old) == compared_grant(strip(old))
    if phrase:
        assert phrase in row.why
    else:
        assert "launch source" not in row.why


def test_package_publication_does_not_disclose_positional_or_url_secrets():
    secret = "ghp_" + "x" * 36
    for config in [
        {"command": "npx", "args": ["pkg@latest", secret]},
        {"command": "pipx", "args": ["run", "--spec", f"git+https://user:{secret}@example.com/repo.git@main", "server"]},
        {"command": "pipx", "args": ["run", "--spec", "git+https://example.com/private-project.git@main", "server"]},
    ]:
        source = _mcp_launch_source(config)
        assert secret not in json.dumps(source)
        assert "private-project" not in json.dumps(source)


def test_docker_flag_values_are_skipped_and_never_published():
    secret = "ghp_" + "x" * 36
    args = [
        "run", "-i", "--rm", "-e", f"GITHUB_PERSONAL_ACCESS_TOKEN={secret}", "--name", "org/decoy:1.0",
        "-v", "/srv/private-project:/data", "ghcr.io/github/github-mcp-server:latest",
    ]
    assert launch_source_pin("docker", args) == ("mutable", len(args) - 1)
    source = _mcp_launch_source({"command": "docker", "args": args})
    assert source == {"pin": "mutable", "package": "ghcr.io/github/github-mcp-server:latest"}
    # A flag's value is never the selected source, even when it is the only image-shaped argument.
    decoy = ["run", "--name", "org/decoy:1.0", "actual"]
    assert launch_source_pin("docker", decoy) == ("mutable", 3)
    assert _mcp_launch_source({"command": "docker", "args": decoy}) == {"pin": "mutable", "package": None}
    # #819's redaction reads the argument after a secret-named one as its value: a rewritten image abstains.
    redacted = ["run", "--env", "API_KEY", "mcp/fetch:latest"]
    assert launch_source_pin("docker", redacted) == ("mutable", 3)
    assert _mcp_launch_source({"command": "docker", "args": redacted}) is None


@pytest.mark.parametrize("path,contents", [
    (".mcp.json", lambda server: json.dumps({"mcpServers": {"docs": server}})),
    (".cursor/mcp.json", lambda server: json.dumps({"mcpServers": {"docs": server}})),
    (".vscode/mcp.json", lambda server: json.dumps({"servers": {"docs": server}})),
    (".codex/config.toml", lambda server: '[mcp_servers.docs]\ncommand="npx"\nargs=' + json.dumps(server["args"]) + '\n'),
])
def test_text_json_verify_and_pr_comment_agree(tmp_path, path, contents, monkeypatch):
    root = tmp_path / "repo"
    target = root / path
    target.parent.mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    def git(*args):
        subprocess.run(["git", "-C", str(root), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args], env=env, check=True, capture_output=True)
    def invoke(*args):
        result = CliRunner().invoke(app, [*args, "--workspace", str(root)])
        assert result.exit_code in (0, 10, 20), result.output
        return result.stdout
    git("init", "-q", "-b", "main")
    target.write_text(contents({"command": "npx", "args": ["pkg@1.2.3"]}))
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "change")
    target.write_text(contents({"command": "npx", "args": ["pkg@latest"]}))
    git("add", "-A")
    git("commit", "-qm", "head")
    diff = json.loads(invoke("diff", "--base", "main", "--json"))
    text = invoke("diff", "--base", "main")
    verify = json.loads(invoke("verify", "--base", "main", "--head", "HEAD", "--json"))
    assert diff["rows"] == verify["host_comparison"]["rows"]
    note = "launch source moved from pinned (pkg@1.2.3) to mutable (pkg@latest)"
    assert note in diff["rows"][0]["why"] and note in text
    assert note in (root / "agents-shipgate-reports/pr-comment.md").read_text()
    check = json.loads(invoke("check", "--agent", "codex", "--base", "main", "--format", "agent-boundary-json"))
    monkeypatch.setattr("agents_shipgate.core.host_grants._mcp_launch_source", lambda config: None)
    original = json.loads(invoke("check", "--agent", "codex", "--base", "main", "--format", "agent-boundary-json"))
    for key in ("decision", "control", "violations"):
        assert check[key] == original[key]


def test_git_ref_classification_does_not_expand_the_url_path_comparison_boundary():
    # Known limit, tracked by #772: config_sha256 covers the redacted URL, never its path, so a Git
    # ref moving from a pinned commit to a branch inside that path produces no row and so no note.
    # Both pin states are established, but launch_source is display-only and cannot create a row.
    old = grant({"command": "pipx", "args": ["run", "--spec", "git+https://example.com/repo.git@" + SHA, "server"]})
    new = grant({"command": "pipx", "args": ["run", "--spec", "git+https://example.com/repo.git@main", "server"]})
    assert old["launch_source"] == {"pin": "pinned", "package": None}
    assert new["launch_source"] == {"pin": "mutable", "package": None}
    assert old["config_sha256"] == new["config_sha256"]
    assert diff_host_grants({"grants": [old]}, {"grants": [new]}) == []
