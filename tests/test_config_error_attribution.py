"""A configuration failure names the side that has to act (#328).

``verify --head`` reads the commit through an archive that does not write
every entry a valid Git tree can hold. Its refusal reached the manifest
diagnostics, which found ``shipgate.yaml`` on disk and told the reader — and,
through the GitHub Action, every CI run — to fix the manifest in place, though
neither the manifest nor the repository was at fault. Such a refusal is now
classified as Agents Shipgate's limitation (``details.origin``) and routed to
a review that asks for no edit; a manifest the loader rejects keeps its edit.

A schema violation caused by YAML syntax names the cause too: an unquoted
``: `` in a prose list item makes the item a mapping, and the reader was told
only that their sentence was not a string.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.config.loader import load_manifest_text
from agents_shipgate.core.errors import ConfigError

runner = CliRunner()

MANIFEST = """\
version: "0.1"
project:
  name: test
agent:
  name: test-agent
  declared_purpose:
    - {purpose}
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
"""

TOOLS = '{"tools":[{"name":"docs.lookup","annotations":{"readOnlyHint":true}}]}\n'

#: An unquoted ``: `` inside the one field a person has to write in prose.
COLON_PURPOSE = (
    "Handle quote-to-cash requests from natural language: read opportunities, "
    "create quotes, and create sales orders."
)

AGENT_MODE = {"AGENTS_SHIPGATE_AGENT_MODE": "1"}
HUMAN_MODE = {"AGENTS_SHIPGATE_AGENT_MODE": "0"}


def _git(repo: Path, *args: str, data: bytes | None = None) -> str:
    # `-C`, as the root conftest expects of fixture Git (#1218).
    return subprocess.run(
        ["git", "-C", str(repo), *args], input=data, check=True, capture_output=True
    ).stdout.decode().strip()


def _repo(tmp_path: Path, *, purpose: str = "test") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "shipgate.yaml").write_text(MANIFEST.format(purpose=purpose), encoding="utf-8")
    (repo / "tools.json").write_text(TOOLS, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _commit_on_top(repo: Path, extra: dict[str, tuple[str, bytes]]) -> None:
    """Commit HEAD's tree plus ``extra`` (path -> (mode, bytes)) through plumbing.

    A ``160000`` entry's bytes are ignored: it points at HEAD, as a submodule
    pointer names a commit. The worktree is left alone, so a tree that this
    filesystem cannot hold can still be committed on it.
    """

    nested: dict = {}

    def place(path: str, value: tuple[str, str, str]) -> None:
        *parents, name = path.split("/")
        node = nested
        for part in parents:
            node = node.setdefault(part, {})
        node[name] = value

    for record in _git(repo, "ls-tree", "-r", "-z", "HEAD").split("\0"):
        if record:
            metadata, path = record.split("\t", 1)
            mode, kind, oid = metadata.split(" ")
            place(path, (mode, kind, oid))
    head = _git(repo, "rev-parse", "HEAD")
    for path, (mode, data) in extra.items():
        if mode == "160000":
            place(path, (mode, "commit", head))
        else:
            place(path, (mode, "blob", _git(repo, "hash-object", "-w", "--stdin", data=data)))

    def write(node: dict) -> str:
        lines = [
            f"040000 tree {write(value)}\t{name}"
            if isinstance(value, dict)
            else f"{value[0]} {value[1]} {value[2]}\t{name}"
            for name, value in node.items()
        ]
        return _git(repo, "mktree", data=("\n".join(lines) + "\n").encode())

    commit = _git(repo, "commit-tree", write(nested), "-p", "HEAD", "-m", "plumbed")
    _git(repo, "update-ref", "HEAD", commit)


def _verify_head(repo: Path, out: Path, env: dict[str, str]):
    return runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--json", "--out", str(out),
         "--base", "HEAD~1", "--head", "HEAD"],
        env=env,
    )


def _error_line(result) -> dict:
    """The one agent-mode JSON line on stderr."""

    lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    assert len(lines) == 1, result.stderr
    return json.loads(lines[0])


def _assert_limitation_route(payload: dict, *, path: str, refusal: str) -> None:
    assert payload["error"] == "config_error"
    assert payload["exit_code"] == 2
    assert payload["message"] == refusal
    assert payload["details"] == {
        "failure": "unsupported_git_tree_entry",
        "origin": "agents_shipgate",
        "path": path,
    }
    actions = payload["next_actions"]
    assert [action["kind"] for action in actions] == ["review"]
    for action in actions:
        assert action["path"] is None
        assert action["command"] is None
    why = actions[0]["why"]
    assert why.startswith(refusal.rstrip("."))
    assert f"Git holds {path} as part of a valid tree" in why
    assert "This is a limitation of Agents Shipgate" in why
    assert "neither the manifest nor the repository needs to change" in why
    assert "without --head" in why
    assert "Fix the manifest" not in why
    assert payload["next_action"] == f"Review: {why}"


# Each kind of committed entry the verification archive refuses, all in a tree
# Git's own strict check accepts.
_REFUSED_TREES = {
    "link-leaving-the-tree": (
        {"VERSION": ("120000", b"../outside")},
        "VERSION",
        "Git tree contains unsupported external binding at VERSION (mode 120000, type blob): ",
    ),
    "submodule": (
        {"vendor/lib": ("160000", b"")},
        "vendor/lib",
        "Git tree contains unsupported external binding at vendor/lib "
        "(mode 160000, type commit).",
    ),
    "names-a-filesystem-merges": (
        {"README": ("100644", b"upper\n"), "readme": ("100644", b"lower\n")},
        "readme",
        "Git tree contains filesystem-colliding paths: 'README' and 'readme'",
    ),
    "name-not-portable": (
        {"notes/a:b.md": ("100644", b"note\n")},
        "notes/a:b.md",
        "Git tree path is not portable: notes/a:b.md",
    ),
}


@pytest.mark.parametrize("case", sorted(_REFUSED_TREES))
def test_an_unsupported_committed_entry_is_not_routed_to_the_manifest(tmp_path, case):
    extra, path, refusal = _REFUSED_TREES[case]
    repo = _repo(tmp_path)
    _commit_on_top(repo, extra)

    result = _verify_head(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 2, result.output
    payload = _error_line(result)
    assert payload["message"].startswith(refusal)
    _assert_limitation_route(payload, path=path, refusal=payload["message"])
    assert "shipgate.yaml" not in json.dumps(payload["next_actions"]).replace(
        "not an error in shipgate.yaml", ""
    )


def test_a_manifest_name_a_filesystem_merges_is_a_limitation(tmp_path):
    # Both spellings are valid Git entries; which one is the manifest cannot
    # be settled portably, and that is not the manifest's content at fault.
    repo = _repo(tmp_path)
    _commit_on_top(repo, {"SHIPGATE.yaml": ("100644", b"other\n")})

    result = _verify_head(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 2, result.output
    _assert_limitation_route(
        _error_line(result),
        path="shipgate.yaml",
        refusal=(
            "Git tree contains filesystem-colliding paths for configured manifest "
            "'shipgate.yaml': ['SHIPGATE.yaml', 'shipgate.yaml']"
        ),
    )


def test_a_committed_link_refused_on_windows_is_a_limitation(tmp_path, monkeypatch):
    # Where links are not recreated, an in-tree link is refused too; that is
    # the platform's limitation, not the manifest's.
    from agents_shipgate.cli.verify import git as git_module

    monkeypatch.setattr(git_module, "_recreates_committed_links", lambda: False)
    repo = _repo(tmp_path)
    _commit_on_top(repo, {
        "AGENTS.md": ("100644", b"# Agents\n"),
        "CLAUDE.md": ("120000", b"AGENTS.md"),
    })

    result = _verify_head(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 2, result.output
    _assert_limitation_route(
        _error_line(result),
        path="CLAUDE.md",
        refusal=(
            "Git tree contains unsupported external binding at CLAUDE.md "
            "(mode 120000, type blob): committed links are not recreated on Windows."
        ),
    )


def test_a_person_reading_the_console_is_not_told_to_edit_the_manifest(tmp_path):
    repo = _repo(tmp_path)
    _commit_on_top(repo, {"vendor/lib": ("160000", b"")})

    result = _verify_head(repo, tmp_path / "out", HUMAN_MODE)

    assert result.exit_code == 2, result.output
    assert not [line for line in result.stderr.splitlines() if line.startswith("{")]
    hints = [line for line in result.stderr.splitlines() if line.startswith("next: ")]
    assert len(hints) == 1, result.stderr
    assert hints[0].startswith(
        "next: Review: Git tree contains unsupported external binding at vendor/lib"
    )
    assert "This is a limitation of Agents Shipgate" in hints[0]
    assert "Edit " not in result.stderr
    assert "Fix the manifest" not in result.stderr


def test_verification_prepare_routes_the_refusal_the_same_way(tmp_path):
    repo = _repo(tmp_path)
    _commit_on_top(repo, {"vendor/lib": ("160000", b"")})

    result = runner.invoke(
        app,
        ["verification", "prepare", "--workspace", str(repo), "--base", "HEAD~1",
         "--head", "HEAD", "--out", str(tmp_path / "plan.json")],
        env=AGENT_MODE,
    )

    assert result.exit_code == 2, result.output
    _assert_limitation_route(
        _error_line(result),
        path="vendor/lib",
        refusal=(
            "Git tree contains unsupported external binding at vendor/lib "
            "(mode 160000, type commit)."
        ),
    )


def test_a_manifest_the_loader_rejects_still_routes_to_its_edit(tmp_path):
    # The counterexample: a malformed manifest is the adopter's to fix, and the
    # archived head reaches the loader, so the edit route and its cause stay.
    repo = _repo(tmp_path)
    (repo / "shipgate.yaml").write_text(
        MANIFEST.format(purpose=COLON_PURPOSE), encoding="utf-8"
    )
    _git(repo, "commit", "-q", "-am", "describe the agent")

    result = _verify_head(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 2, result.output
    payload = _error_line(result)
    assert payload["error"] == "config_error"
    assert "details" not in payload
    edit = payload["next_actions"][0]
    assert edit["kind"] == "edit"
    assert Path(edit["path"]).name == "shipgate.yaml"
    assert "Fix the manifest in place" in edit["why"]
    assert "agent.declared_purpose.0: Input should be a valid string" in edit["why"]
    assert "put it in quotes" in edit["why"]


# --- the unquoted colon -------------------------------------------------------


def _load_error(purpose_block: str) -> str:
    text = MANIFEST.replace("    - {purpose}\n", purpose_block)
    with pytest.raises(ConfigError) as refused:
        load_manifest_text(text)
    return str(refused.value)


@pytest.mark.parametrize(
    "item",
    [f"    - {COLON_PURPOSE}\n", "    - Handle refunds:\n"],
    ids=["colon-inside", "colon-at-end"],
)
def test_an_unquoted_colon_in_a_prose_item_names_its_cause(item):
    message = _load_error(item)

    assert message.splitlines() == [
        "Invalid shipgate.yaml:",
        "- agent.declared_purpose.0: Input should be a valid string",
        "Hint: agent.declared_purpose.0 is a key-value mapping, not a single value. "
        "An unquoted ': ' inside text makes YAML read the words before it as a key; "
        "if the value is text that contains ': ', put it in quotes.",
    ]
    # The hint names the field, never the value someone wrote.
    assert "quote-to-cash" not in message
    assert "refunds" not in message


def test_quoting_the_value_clears_the_error():
    manifest = load_manifest_text(MANIFEST.format(purpose=json.dumps(COLON_PURPOSE)))

    assert manifest.agent.declared_purpose == [COLON_PURPOSE]


@pytest.mark.parametrize(
    "item",
    [
        "    - 42\n",
        "    - {first: one, second: two}\n",
        "    - Handle refunds:\n        steps: [read, credit]\n",
        "    - [nested, list]\n",
    ],
    ids=["number", "two-keys", "nested-mapping", "list"],
)
def test_other_validation_errors_get_no_colon_hint(item):
    message = _load_error(item)

    assert message.startswith("Invalid shipgate.yaml:\n- agent.declared_purpose.0: ")
    assert "Hint:" not in message
    assert "': '" not in message


def test_a_misspelled_field_keeps_its_suggestion_and_gets_no_colon_hint():
    text = MANIFEST.format(purpose="test").replace("  declared_purpose:", "  declared_purpos:")

    with pytest.raises(ConfigError) as refused:
        load_manifest_text(text)

    message = str(refused.value)
    assert "Did you mean declared_purpose?" in message
    assert "Hint:" not in message
