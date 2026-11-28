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
import os
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


def _commit_on_top(repo: Path, extra: dict[bytes, tuple[str, bytes]]) -> None:
    """Commit HEAD's tree plus ``extra`` (path -> (mode, bytes)) through plumbing.

    Paths are bytes, so a name that is not UTF-8 can be committed. A
    ``160000`` entry's bytes are ignored: it points at HEAD, as a submodule
    pointer names a commit. The worktree is left alone, so a tree that this
    filesystem cannot hold can still be committed on it.
    """

    nested: dict = {}

    def place(path: bytes, value: tuple[bytes, bytes, bytes]) -> None:
        *parents, name = path.split(b"/")
        node = nested
        for part in parents:
            node = node.setdefault(part, {})
        node[name] = value

    listing = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", "HEAD"],
        check=True, capture_output=True,
    ).stdout
    for record in listing.split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            mode, kind, oid = metadata.split(b" ")
            place(path, (mode, kind, oid))
    head = _git(repo, "rev-parse", "HEAD").encode()
    for path, (mode, data) in extra.items():
        if mode == "160000":
            place(path, (b"160000", b"commit", head))
        else:
            oid = _git(repo, "hash-object", "-w", "--stdin", data=data).encode()
            place(path, (mode.encode(), b"blob", oid))

    def write(node: dict) -> bytes:
        records = [
            b"040000 tree " + write(value) + b"\t" + name
            if isinstance(value, dict)
            else b" ".join(value) + b"\t" + name
            for name, value in node.items()
        ]
        return _git(repo, "mktree", "-z", data=b"\0".join(records) + b"\0").encode()

    commit = _git(repo, "commit-tree", write(nested).decode(), "-p", "HEAD", "-m", "plumbed")
    _git(repo, "update-ref", "HEAD", commit)


def _verify(repo: Path, out: Path, env: dict[str, str], *args: str):
    return runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--json", "--out", str(out),
         "--base", "HEAD~1", *args],
        env=env,
    )


def _verify_head(repo: Path, out: Path, env: dict[str, str]):
    return _verify(repo, out, env, "--head", "HEAD")


def _prepare_head(repo: Path, plan: Path):
    return runner.invoke(
        app,
        ["verification", "prepare", "--workspace", str(repo), "--base", "HEAD~1",
         "--head", "HEAD", "--out", str(plan)],
        env=AGENT_MODE,
    )


def _error_line(result) -> dict:
    """The one agent-mode JSON line on stderr."""

    lines = [line for line in result.stderr.splitlines() if line.startswith("{")]
    assert len(lines) == 1, result.stderr
    return json.loads(lines[0])


UNSUPPORTED = "unsupported_git_tree_entry"
UNCONTAINED = "uncontained_git_tree_link"
LISTING = "git_tree_listing_unreadable"


def _assert_limitation_route(
    payload: dict, *, failure: str, path: str | None, refusal: str
) -> str:
    """The route every refusal Agents Shipgate owns takes; returns its ``why``."""

    assert payload["error"] == "config_error"
    assert payload["exit_code"] == 2
    assert payload["message"] == refusal
    expected = {"failure": failure, "origin": "agents_shipgate"}
    if path is not None:
        expected["path"] = path
    assert payload["details"] == expected
    actions = payload["next_actions"]
    assert [action["kind"] for action in actions] == ["review"]
    for action in actions:
        assert action["path"] is None
        assert action["command"] is None
    why = actions[0]["why"]
    assert why.startswith(refusal.rstrip("."))
    assert "shipgate.yaml" not in why.replace("not an error in shipgate.yaml", "").replace(
        "nothing in shipgate.yaml is wrong", ""
    )
    assert "Fix the manifest" not in why
    assert payload["next_action"] == f"Review: {why}"
    if failure == UNSUPPORTED:
        assert f"Git holds {path} as part of a valid tree" in why
        assert "does not support it yet" in why
        assert "neither the manifest nor the repository needs to change" in why
        assert "without --head from a checkout" in why
        assert "report it at https://github.com/ThreeMoonsLab/agents-shipgate/issues" in why
    elif failure == UNCONTAINED:
        # Refused by design: no promise a later release lifts it, and no report.
        assert "deliberately recreates only links that open a file or directory" in why
        assert "nothing in shipgate.yaml is wrong" in why
        assert "without --head from a checkout" in why
        assert (
            f"if {path} is not meant to point outside the repository or at a path "
            "the commit does not hold"
        ) in why
        assert "report" not in why
        assert payload["next_actions"][0]["expects"] == (
            "A run that reads the commit from a checkout."
        )
    else:
        # No run of the commit gets past it, so nothing is offered that cannot help.
        assert "with or without --head" in why
        assert "without --head from a checkout" not in why
        assert "Report it at https://github.com/ThreeMoonsLab/agents-shipgate/issues" in why
    return why


#: Each kind of committed entry the --head archive refuses, in a tree Git's
#: own strict check accepts: (extra entries, failure, refused path, message).
_REFUSED_TREES = {
    "link-leaving-the-tree": (
        {b"VERSION": ("120000", b"../outside")},
        UNSUPPORTED if os.name == "nt" else UNCONTAINED,
        "VERSION",
        "Git tree contains unsupported external binding at VERSION (mode 120000, type blob): "
        + (
            "committed links are not recreated on Windows."
            if os.name == "nt"
            else "only a link that opens a file or directory inside the repository is recreated."
        ),
    ),
    "submodule": (
        {b"vendor/lib": ("160000", b"")},
        UNSUPPORTED,
        "vendor/lib",
        "Git tree contains unsupported external binding at vendor/lib "
        "(mode 160000, type commit).",
    ),
    "names-a-filesystem-merges": (
        {b"README": ("100644", b"upper\n"), b"readme": ("100644", b"lower\n")},
        UNSUPPORTED,
        "readme",
        "Git tree contains filesystem-colliding paths: 'README' and 'readme'",
    ),
    "name-not-portable": (
        {b"notes/a:b.md": ("100644", b"note\n")},
        UNSUPPORTED,
        "notes/a:b.md",
        "Git tree path is not portable: notes/a:b.md",
    ),
    # No route records a backslash in a path, the checkout run included, so it
    # is not offered as one.
    "name-with-a-backslash": (
        {b"notes/a\\b.md": ("100644", b"note\n")},
        LISTING,
        "notes/a\\b.md",
        "Git tree path is not portable: notes/a\\b.md",
    ),
}


@pytest.mark.parametrize("case", sorted(_REFUSED_TREES))
def test_an_unsupported_committed_entry_is_not_routed_to_the_manifest(tmp_path, case):
    extra, failure, path, refusal = _REFUSED_TREES[case]
    repo = _repo(tmp_path)
    _commit_on_top(repo, extra)

    result = _verify_head(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 2, result.output
    _assert_limitation_route(_error_line(result), failure=failure, path=path, refusal=refusal)


@pytest.mark.parametrize(
    "case", ["submodule", "name-not-portable", "name-with-a-backslash"]
)
def test_verification_prepare_routes_the_refusal_the_same_way(tmp_path, case):
    # `prepare` archives the head without the manifest-spelling read `verify`
    # does first, so the archive's own name checks are what it meets.
    extra, failure, path, refusal = _REFUSED_TREES[case]
    repo = _repo(tmp_path)
    _commit_on_top(repo, extra)

    result = _prepare_head(repo, tmp_path / "plan.json")

    assert result.exit_code == 2, result.output
    _assert_limitation_route(_error_line(result), failure=failure, path=path, refusal=refusal)


@pytest.mark.skipif(os.name == "nt", reason="the name cannot be checked out on Windows")
def test_a_name_only_the_head_snapshot_refuses_does_not_stop_a_checkout_run(tmp_path):
    # Reading the manifest's exact spelling used to refuse any non-portable
    # name anywhere in the tree, so the "run without --head" route failed the
    # same way. Such a name cannot be a spelling of the manifest; a run on the
    # checkout reads it as the checkout holds it.
    repo = _repo(tmp_path)
    _commit_on_top(repo, {b"notes/a:b.md": ("100644", b"note\n")})
    _git(repo, "reset", "-q", "--hard")

    result = _verify(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["head_status"] == "succeeded"


@pytest.mark.skipif(os.name == "nt", reason="the name cannot be checked out on Windows")
def test_a_backslash_name_is_refused_on_a_checkout_run_too(tmp_path):
    # The checkout run records every changed path in the receipt, whose paths
    # are portable, so it refuses this name as the --head snapshot does: as a
    # classified limitation, never an internal error.
    repo = _repo(tmp_path)
    _commit_on_top(repo, {b"notes/a\\b.md": ("100644", b"note\n")})
    _git(repo, "reset", "-q", "--hard")

    result = _verify(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 2, result.output
    payload = _error_line(result)
    _assert_limitation_route(
        payload,
        failure=LISTING,
        path="notes/a\\b.md",
        refusal="Git tree path is not portable: notes/a\\b.md",
    )
    for text in ("internal_error", "Internal error", "validation error", "errors.pydantic.dev"):
        assert text not in result.stderr


#: A committed name holding a line break, shaped like a recovery line.
_FORGING_NAME = b"notes/x\nnext: Edit shipgate.yaml"


def test_a_committed_name_is_echoed_on_one_line(tmp_path):
    repo = _repo(tmp_path)
    _commit_on_top(repo, {_FORGING_NAME: ("100644", b"x\n")})

    agent = _verify_head(repo, tmp_path / "out-agent", AGENT_MODE)
    human = _verify_head(repo, tmp_path / "out-human", HUMAN_MODE)

    assert agent.exit_code == human.exit_code == 2, (agent.output, human.output)
    payload = _error_line(agent)
    shown = "notes/x\\x0anext: Edit shipgate.yaml"
    assert payload["details"]["path"] == shown
    why = payload["next_actions"][0]["why"]
    assert "\n" not in why
    assert f"Git holds {shown} as part of a valid tree" in why
    [hint] = [line for line in human.stderr.splitlines() if line.startswith("next: Review:")]
    assert shown in hint
    assert hint.endswith("with this message.")


@pytest.mark.skipif(os.name == "nt", reason="the name cannot be checked out on Windows")
def test_a_control_character_name_does_not_stop_a_checkout_run(tmp_path):
    # The route offered for that name holds: a run on the checkout reads it.
    repo = _repo(tmp_path)
    _commit_on_top(repo, {_FORGING_NAME: ("100644", b"x\n")})
    _git(repo, "reset", "-q", "--hard")

    result = _verify(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["head_status"] == "succeeded"


@pytest.mark.parametrize("head", [True, False], ids=["head", "checkout"])
def test_a_name_that_is_not_utf8_is_a_limitation_on_every_route(tmp_path, head):
    repo = _repo(tmp_path)
    _commit_on_top(repo, {b"bad\xff.txt": ("100644", b"x\n")})

    result = _verify(repo, tmp_path / "out", AGENT_MODE, *(("--head", "HEAD") if head else ()))

    assert result.exit_code == 2, result.output
    why = _assert_limitation_route(
        _error_line(result),
        failure=LISTING,
        path="bad\\xff.txt",
        refusal="Git tree contains a non-UTF-8 path",
    )
    assert "(bad\\xff.txt)" in why


def test_a_path_listing_past_its_bound_is_a_limitation(tmp_path, monkeypatch):
    from agents_shipgate.cli.verify import git as git_module

    monkeypatch.setattr(git_module, "_MAX_MANIFEST_LISTING_BYTES", 10)
    repo = _repo(tmp_path)
    _commit_on_top(repo, {b"notes.md": ("100644", b"x\n")})

    result = _verify(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 2, result.output
    _assert_limitation_route(
        _error_line(result),
        failure=LISTING,
        path=None,
        refusal=(
            "Git tree path identity could not be established within static resource bounds."
        ),
    )


def test_two_spellings_of_the_manifest_are_the_repository_to_settle(tmp_path):
    # Every route refuses this commit, so it is not a limitation to wait out:
    # the route names both spellings and asks for one to be kept.
    repo = _repo(tmp_path)
    _commit_on_top(repo, {b"SHIPGATE.yaml": ("100644", b"other\n")})

    result = _verify_head(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 2, result.output
    payload = _error_line(result)
    assert payload["message"] == (
        "Git tree contains filesystem-colliding paths for configured manifest "
        "'shipgate.yaml': ['SHIPGATE.yaml', 'shipgate.yaml']"
    )
    assert "details" not in payload
    [action] = payload["next_actions"]
    assert action["kind"] == "review"
    assert action["path"] is None
    assert "The commit holds SHIPGATE.yaml and shipgate.yaml" in action["why"]
    assert "Keep one spelling: remove or rename the other" in action["why"]
    assert "limitation" not in action["why"]
    assert "Fix the manifest" not in action["why"]


def test_a_committed_link_refused_on_windows_is_a_limitation(tmp_path, monkeypatch):
    # Where links are not recreated, an in-tree link is refused too; that is
    # the platform's limitation, not the manifest's.
    from agents_shipgate.cli.verify import git as git_module

    monkeypatch.setattr(git_module, "_recreates_committed_links", lambda: False)
    repo = _repo(tmp_path)
    _commit_on_top(repo, {
        b"AGENTS.md": ("100644", b"# Agents\n"),
        b"CLAUDE.md": ("120000", b"AGENTS.md"),
    })

    result = _verify_head(repo, tmp_path / "out", AGENT_MODE)

    assert result.exit_code == 2, result.output
    _assert_limitation_route(
        _error_line(result),
        failure=UNSUPPORTED,
        path="CLAUDE.md",
        refusal=(
            "Git tree contains unsupported external binding at CLAUDE.md "
            "(mode 120000, type blob): committed links are not recreated on Windows."
        ),
    )


@pytest.mark.skipif(os.name == "nt", reason="committed links are not recreated on Windows")
def test_a_link_the_filesystem_will_not_create_is_not_classified(tmp_path, monkeypatch):
    # The filesystem refusing the write is not a property of the tree, so it
    # carries no tree classification and keeps the generic route.
    from agents_shipgate.cli.verify.git import archive_verification_tree

    repo = _repo(tmp_path)
    _commit_on_top(repo, {
        b"AGENTS.md": ("100644", b"# Agents\n"),
        b"CLAUDE.md": ("120000", b"AGENTS.md"),
    })

    def refuse(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("agents_shipgate.cli.verify.git.os.symlink", refuse)
    with pytest.raises(ConfigError) as refused:
        archive_verification_tree(repo, "HEAD", tmp_path / "snapshot")

    assert str(refused.value) == (
        "Git tree contains unsupported external binding at CLAUDE.md "
        "(mode 120000, type blob): this system could not create the link."
    )
    assert refused.value.details == {}


def test_a_person_reading_the_console_is_not_told_to_edit_the_manifest(tmp_path):
    repo = _repo(tmp_path)
    _commit_on_top(repo, {b"vendor/lib": ("160000", b"")})

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
