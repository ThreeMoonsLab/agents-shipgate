"""#1004: an unchanged in-tree directory link the reader leaves unread is named, not a veto.

`chanzuckerberg/single-cell-data-portal#7802` changes only `.mcp.json`. Its
`.happy/terraform/envs/dev/modules -> ../../modules` is an in-tree directory
link the reader does not read through (#700): it is not at a boundary
location, and a `**/` glob such as `**/CLAUDE.md` could match beneath it, so
it is an `unreadable` limit under every host with such a glob. Unchanged on
both sides, it still refused the comparison, `incomparable` with no row,
because `unreadable` was never named as an unchanged limit (#721, #822).

Owner ruling (2026-11-15): an unchanged in-tree link, identical on both sides
by Git identity, is named as an unchanged unread limit and the independently
established rows are kept. Retargeted links, changed target trees, new links,
loops, chains and external bindings keep their limits, and no link is
followed. An unchanged link at, under or above a path a host reads for agent
configuration keeps its blocking limit, so configuration cannot be moved
behind a link.

"Unchanged" is proven from Git objects only: the link's mode and blob (its
text), and the tree object of the in-tree directory it names, with no link or
submodule beneath it. A working-tree head is read without following the link:
each component by its own entry, the link by its own text, and every file
beneath the directory it names by its own entry and unfiltered hash.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.git import _linked_directory, unread_link_unchanged
from agents_shipgate.core.boundary_registry import is_host_configuration_location
from agents_shipgate.core.host_comparison import UNCHANGED_LINK_DETAIL, unchanged_limits
from agents_shipgate.core.host_grants import host_audit_inventory

pytestmark = pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")

TARGET = ".happy/terraform/modules"
LINK = ".happy/terraform/envs/dev/modules"
TEXT = "../../modules"
MODULE = 'variable "example" { type = string }\n'
MCP_BASE = '{"mcpServers": {}}\n'
MCP_HEAD = json.dumps(
    {"mcpServers": {"example": {
        "command": "npx", "args": ["-y", "example-mcp@latest"], "env": {"API_TOKEN": "placeholder"},
    }}}
) + "\n"
#: The hosts whose `**/` globs the link may conceal, each naming it once.
LINK_HOSTS = {"claude-code", "codex", "cursor", "openshell"}
BOTH = ["base_inventory_incomplete", "head_inventory_incomplete"]
NOT_COMPARED = "Not compared: unchanged in this change and not read, so no claim is made about them:"
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
_CLI_ENV = {
    "NO_COLOR": "1", "COLUMNS": "200",
    "AGENTS_SHIPGATE_CLI": None, "AGENTS_SHIPGATE_ENABLE_PLUGINS": None,
    "CLAUDECODE": None, "CURSOR_TRACE_ID": None,
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _remove(repo: Path, name: str) -> None:
    """Remove what is at ``name`` itself, never following a link there."""

    path = repo / name
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _link(repo: Path, name: str, target: str) -> None:
    _remove(repo, name)
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _repository(
    tmp_path: Path,
    files: dict[str, str] | None = None,
    links: dict[str, str] | None = None,
    *,
    head_removed: tuple[str, ...] = (),
    head_files: dict[str, str] | None = None,
    head_links: dict[str, str] | None = None,
    head_executable: tuple[str, ...] = (),
    head_mcp: str = MCP_HEAD,
) -> Path:
    """`main` holds the issue's Terraform layout beside ``files`` and ``links``; `change` adds an MCP server.

    ``files`` and ``links`` default to the issue's module and its link. On
    `change`, ``head_removed`` paths are removed first, then ``head_files``
    and ``head_links`` are written and ``head_executable`` files made
    executable, and all of it is committed. A link text ``{outside}`` names
    a directory beside the repository holding the same module.
    """

    outside = tmp_path / "outside"
    _write(outside, "modules/main.tf", MODULE)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, ".mcp.json", MCP_BASE)
    _write(repo, ".gitignore", "agents-shipgate-reports/\n*.local\n")
    for name, text in ({f"{TARGET}/main.tf": MODULE} if files is None else files).items():
        _write(repo, name, text)
    for name, target in ({LINK: TEXT} if links is None else links).items():
        _link(repo, name, target.format(outside=outside))
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "change")
    _write(repo, ".mcp.json", head_mcp)
    for name in head_removed:
        _remove(repo, name)
    for name, text in (head_files or {}).items():
        _write(repo, name, text)
    for name, target in (head_links or {}).items():
        _link(repo, name, target.format(outside=outside))
    for name in head_executable:
        os.chmod(repo / name, 0o755)
    _commit(repo, "change")
    return repo


def _invoke(args: list[str]) -> str:
    result = CliRunner().invoke(app, args, env=_CLI_ENV)
    assert result.exit_code == 0, result.output
    return result.output


def _diff(repo: Path) -> dict:
    return json.loads(_invoke(["diff", "--workspace", str(repo), "--base", "main", "--json"]))


def _check(repo: Path) -> dict:
    return json.loads(_invoke([
        "check", "--workspace", str(repo), "--base", "main", "--head", "HEAD",
        "--format", "agent-boundary-json",
    ]))


def _all_routes(repo: Path, tmp_path: Path) -> tuple[str, dict, str, str]:
    """diff text and JSON, and verify's comparison, text and PR comment, agreeing on one comparison."""

    command = ["diff", "--workspace", str(repo), "--base", "main"]
    text, payload = _invoke(command), json.loads(_invoke([*command, "--json"]))
    out = tmp_path / "out"
    verify_text = _invoke([
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--format", "text", "--base", "main",
        "--head", _git(repo, "rev-parse", "HEAD"),
    ])
    comparison = json.loads((out / "verifier.json").read_text(encoding="utf-8"))["host_comparison"]
    for key in ("comparison_status", "incomparable_reasons", "rows", "unchanged_limits", "coverage"):
        assert comparison[key] == payload[key], key
    if payload["review"] is not None:
        assert comparison["review"]["changes"] == payload["review"]["changes"]
    comment = (out / "pr-comment.md").read_text(encoding="utf-8")
    return text, payload, verify_text, comment


def _rows(payload: dict) -> list[tuple[str, str, str]]:
    return [(row["subject"], row["after"], row["direction"]) for row in payload["rows"]]


def _limits(payload: dict) -> set[tuple[str, str, str]]:
    return {(limit["host"], limit["source"], limit["limit"]) for limit in payload["unchanged_limits"]}


def _named(link: str = LINK) -> set[tuple[str, str, str]]:
    return {(host, link, "unreadable") for host in LINK_HOSTS}


MCP_ADDED = [("claude-code .mcp.json", "example", "added")]


# --- the issue's reproduction -----------------------------------------------------


def test_the_issue_reproduction_keeps_the_mcp_row_and_names_the_link() -> None:
    """The issue's own script, run in-process: `False comparable 1`, `True comparable 1`.

    Before the fix the linked repository printed `True incomparable 0`.
    """

    root = Path(tempfile.mkdtemp()).resolve()
    try:
        observed = []
        for linked in (False, True):
            p = root / ("linked" if linked else "control")
            p.mkdir()

            def git(*args: str, p: Path = p) -> str:
                return subprocess.check_output([
                    "git", "-C", str(p), "-c", "core.hooksPath=/dev/null",
                    "-c", "user.name=Repro", "-c", "user.email=repro@example.invalid",
                    *args], text=True, env=_GIT_ENV).strip()

            git("init", "-q", "-b", "main")
            modules = p / ".happy/terraform/modules"
            modules.mkdir(parents=True)
            (modules / "main.tf").write_text('variable "example" { type = string }\n')
            if linked:
                link = p / ".happy/terraform/envs/dev/modules"
                link.parent.mkdir(parents=True)
                link.symlink_to("../../modules", target_is_directory=True)
            (p / ".mcp.json").write_text('{"mcpServers": {}}\n')
            git("add", "-A")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (p / ".mcp.json").write_text(json.dumps({"mcpServers": {"example": {
                "command": "npx", "args": ["-y", "example-mcp@latest"],
                "env": {"API_TOKEN": "placeholder"}}}}) + "\n")
            git("commit", "-qam", "add MCP")
            d = json.loads(_invoke(["diff", "--workspace", str(p), "--base", base, "--json"]))
            observed.append((linked, d["comparison_status"], len(d["rows"]), d))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    assert [entry[:3] for entry in observed] == [(False, "comparable", 1), (True, "comparable", 1)]
    control, linked = observed[0][3], observed[1][3]
    assert linked["rows"] == control["rows"]
    assert control["unchanged_limits"] == []
    assert _limits(linked) == _named()
    assert {limit["detail"] for limit in linked["unchanged_limits"]} == {UNCHANGED_LINK_DETAIL}
    assert linked["incomparable_reasons"] == []


def test_every_route_names_the_unchanged_link_and_keeps_the_row(tmp_path: Path) -> None:
    repo = _repository(tmp_path)

    text, payload, verify_text, comment = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "comparable"
    assert _rows(payload) == MCP_ADDED
    assert _limits(payload) == _named()
    assert NOT_COMPARED in text
    assert all(f"  {host} {LINK} — unreadable" in text for host in LINK_HOSTS)
    assert "Not compared: unchanged in this change" in verify_text
    assert NOT_COMPARED in comment and f"` {LINK} `" in comment
    assert "example-mcp@latest" in text and "example-mcp@latest" in comment
    # The coverage lists what was compared; the named limit is not repeated.
    assert [item["source"] for item in payload["coverage"]["items"]] == [".mcp.json"]


def test_the_proof_holds_between_commits_and_against_the_working_tree(tmp_path: Path) -> None:
    repo = _repository(tmp_path)

    assert unread_link_unchanged(repo, "main", "HEAD", LINK)
    assert unread_link_unchanged(repo, "main", None, LINK)


def test_check_refuses_as_it_does_for_every_unchanged_limit(tmp_path: Path) -> None:
    """`check`'s boundary result cannot name a limit (#721), so where the other
    routes now compare past the link it refuses with the reason it gives for
    every unchanged limit, instead of `base_inventory_incomplete` /
    `head_inventory_incomplete`. Its rows stay empty and its decision does not
    move."""

    repo = _repository(tmp_path)

    payload = _check(repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == ["unchanged_limits_not_representable"]
    assert payload["rows"] == []
    assert payload["decision"] == "require_review"


def test_audit_still_reports_the_link_and_refuses_a_baseline(tmp_path: Path) -> None:
    """The inventory is unchanged: the link is still a blocking `unreadable`
    issue under each host, so `audit --host --save-baseline` still refuses, and
    `audit --host --drift` has no baseline to compare. A saved baseline
    acknowledges evidence; only a comparison between two commits names an
    unchanged limit (#721)."""

    repo = _repository(tmp_path)

    issues = {
        (issue["kind"], issue["host"], issue["source"], issue["blocking"])
        for issue in host_audit_inventory(repo)["issues"]
    }
    result = CliRunner().invoke(
        app, ["audit", "--host", "--workspace", str(repo), "--save-baseline", "--json"], env=_CLI_ENV
    )

    assert issues == {("unreadable", host, LINK, True) for host in LINK_HOSTS}
    assert result.exit_code == 2
    assert "A baseline cannot acknowledge missing evidence" in result.output
    assert not (repo / ".agents-shipgate").exists()


# --- negative controls: every change to the link or what it names keeps the limit --

EDITED = MODULE.replace("string", "number")
#: ``(base files, base links, what the head changes, reasons)``. Each head also
#: adds the MCP server, so a comparison that compared past the link would
#: publish its row.
CHANGED: dict[str, tuple[dict[str, str] | None, dict[str, str] | None, dict[str, object], list[str]]] = {
    "link-retargeted-to-an-identical-copy": (
        {f"{TARGET}/main.tf": MODULE, ".happy/terraform/copy/main.tf": MODULE},
        None,
        {"head_links": {LINK: "../../copy"}},
        BOTH,
    ),
    "link-text-rewritten-to-name-the-same-directory": (
        None, None, {"head_links": {LINK: "../.././modules"}}, BOTH,
    ),
    "target-file-edited": (None, None, {"head_files": {f"{TARGET}/main.tf": EDITED}}, BOTH),
    "target-file-added": (None, None, {"head_files": {f"{TARGET}/variables.tf": MODULE}}, BOTH),
    "target-file-made-executable": (None, None, {"head_executable": (f"{TARGET}/main.tf",)}, BOTH),
    "link-added": ({f"{TARGET}/main.tf": MODULE}, {}, {"head_links": {LINK: TEXT}}, ["head_inventory_incomplete"]),
    "link-deleted": (None, None, {"head_removed": (LINK,)}, ["base_inventory_incomplete"]),
    "link-replaced-by-a-directory-with-the-same-files": (
        None, None, {"head_removed": (LINK,), "head_files": {f"{LINK}/main.tf": MODULE}},
        ["base_inventory_incomplete"],
    ),
    "directory-replaced-by-a-link-to-the-same-files": (
        {f"{TARGET}/main.tf": MODULE, f"{LINK}/main.tf": MODULE},
        {},
        {"head_removed": (LINK,), "head_links": {LINK: TEXT}},
        ["head_inventory_incomplete"],
    ),
}


@pytest.mark.parametrize("case", list(CHANGED))
def test_a_change_to_the_link_or_what_it_names_keeps_the_limit(tmp_path: Path, case: str) -> None:
    files, links, head, reasons = CHANGED[case]
    repo = _repository(tmp_path, files, links, **head)

    payload = _diff(repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == reasons
    assert payload["rows"] == [] and payload["unchanged_limits"] == []
    assert not unread_link_unchanged(repo, "main", "HEAD", LINK)
    assert not unread_link_unchanged(repo, "main", None, LINK)


#: Unchanged between the commits, and still a limit: the link names no
#: directory inside the repository in one hop, or what it names holds a link.
UNBOUND: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "dangling": ({}, {LINK: TEXT}),
    "external": ({}, {LINK: "{outside}/modules"}),
    "escaping": ({}, {LINK: "../../../../../outside/modules"}),
    "loop-into-its-own-ancestor": ({f"{TARGET}/main.tf": MODULE}, {LINK: ".."}),
    "loop-between-two-links": ({}, {LINK: "../stage/modules", ".happy/terraform/envs/stage/modules": "../dev/modules"}),
    "chain-of-two-links": (
        {f"{TARGET}/main.tf": MODULE},
        {LINK: "../stage/modules", ".happy/terraform/envs/stage/modules": TEXT},
    ),
    "link-above-where-it-lands": (
        {f"{TARGET}/main.tf": MODULE},
        {LINK: "../../alias/modules", ".happy/terraform/alias": "."},
    ),
    "link-inside-the-named-directory": (
        {f"{TARGET}/main.tf": MODULE, ".happy/terraform/shared.tf": MODULE},
        {LINK: TEXT, f"{TARGET}/shared.tf": "../shared.tf"},
    ),
    "missing-component-normalized-away": ({f"{TARGET}/main.tf": MODULE}, {LINK: "missing/../../../modules"}),
}


@pytest.mark.parametrize("shape", list(UNBOUND))
def test_an_unchanged_link_no_git_identity_binds_keeps_the_limit(tmp_path: Path, shape: str) -> None:
    files, links = UNBOUND[shape]
    repo = _repository(tmp_path, files, links)

    payload = _diff(repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["rows"] == [] and payload["unchanged_limits"] == []
    assert "unreadable" in {item["limit"] for item in payload["coverage"]["items"]}
    assert not unread_link_unchanged(repo, "main", "HEAD", LINK)
    assert not unread_link_unchanged(repo, "main", None, LINK)


def test_a_working_tree_change_behind_the_link_keeps_the_limit(tmp_path: Path) -> None:
    """`diff` reads the working tree: an uncommitted edit, an untracked or
    ignored file, or a retarget keeps the limit as a committed one does, and
    restoring the tree names the link again."""

    repo = _repository(tmp_path)
    assert _diff(repo)["comparison_status"] == "comparable"

    _write(repo, f"{TARGET}/main.tf", EDITED)
    assert not unread_link_unchanged(repo, "main", None, LINK)
    assert _diff(repo)["comparison_status"] == "incomparable"
    _write(repo, f"{TARGET}/main.tf", MODULE)
    assert unread_link_unchanged(repo, "main", None, LINK)

    for extra in ("notes.txt", "secrets.local", "nested/deeper/plan.tf"):
        _write(repo, f"{TARGET}/{extra}", "x\n")
        assert not unread_link_unchanged(repo, "main", None, LINK), extra
        _remove(repo, f"{TARGET}/{extra.split('/')[0]}")
    assert unread_link_unchanged(repo, "main", None, LINK)

    _link(repo, f"{TARGET}/inner", "main.tf")
    assert not unread_link_unchanged(repo, "main", None, LINK)
    _remove(repo, f"{TARGET}/inner")

    (repo / ".happy/terraform/copy").mkdir()
    _write(repo, ".happy/terraform/copy/main.tf", MODULE)
    _link(repo, LINK, "../../copy")
    assert not unread_link_unchanged(repo, "main", None, LINK)
    assert _diff(repo)["comparison_status"] == "incomparable"
    _link(repo, LINK, TEXT)
    assert unread_link_unchanged(repo, "main", None, LINK)
    assert _diff(repo)["comparison_status"] == "comparable"


# --- the owner's carve-out: configuration cannot be moved behind a link ------------

#: Unchanged links whose Git identity holds, and which still keep a blocking
#: limit because a host reads agent configuration at, under or above them, or
#: beneath them through the directory they name. ``(files, links, link)``.
CONFIGURATION: dict[str, tuple[dict[str, str], dict[str, str], str]] = {
    # A link directory above `.claude/`, `.codex/` and `.github/workflows/`.
    "above-claude": (
        {"shared/pkg/.claude/settings.json": '{"permissions": {"allow": ["Bash(*)"]}}'},
        {"pkg": "shared/pkg"},
        "pkg",
    ),
    "above-codex": ({"shared/tools/.codex/config.toml": 'model = "o3"\n'}, {"tools": "shared/tools"}, "tools"),
    "above-github-workflows": (
        {"shared/svc/.github/workflows/ci.yml": "on: push\njobs: {}\n"},
        {"svc": "shared/svc"},
        "svc",
    ),
    # The link sits under a configuration directory, holding nothing a host reads.
    "under-claude": ({"docs/agents/notes.txt": "x\n"}, {".claude/agents": "../docs/agents"}, ".claude/agents"),
    "under-nested-claude": ({"docs/notes.txt": "x\n"}, {"pkg/.claude/extra": "../../docs"}, "pkg/.claude/extra"),
    "nested-github-workflows": (
        {"shared/workflows/README.txt": "x\n"},
        {"svc/.github/workflows": "../../shared/workflows"},
        "svc/.github/workflows",
    ),
    "under-agents": ({"docs/notes.txt": "x\n"}, {".agents/extra": "../docs"}, ".agents/extra"),
    # A link that is itself a configuration file's name.
    "named-agents-md": ({"docs/notes.txt": "x\n"}, {"AGENTS.md": "docs"}, "AGENTS.md"),
    "named-nested-claude-md": ({"docs/notes.txt": "x\n"}, {"pkg/CLAUDE.md": "../docs"}, "pkg/CLAUDE.md"),
    "named-mcp-json": ({"docs/notes.txt": "x\n"}, {"pkg/.mcp.json": "../docs"}, "pkg/.mcp.json"),
    # Configuration beneath the directory the link names.
    "holds-claude-md": ({"agent-src/CLAUDE.md": "# rules\n"}, {"docs/agent": "../agent-src"}, "docs/agent"),
    "holds-agents-md": ({"agent-src/sub/AGENTS.md": "# rules\n"}, {"docs/agent": "../agent-src"}, "docs/agent"),
    "holds-plugin-hooks": (
        {"shared/hooks/hooks.json": '{"hooks": {}}'},
        {"plugins/demo/hooks": "../../shared/hooks"},
        "plugins/demo/hooks",
    ),
    "holds-plugin-manifest": (
        {"shared/plugin/.claude-plugin/plugin.json": '{"name": "demo"}'},
        {"plugins/demo": "../shared/plugin"},
        "plugins/demo",
    ),
}


@pytest.mark.parametrize("case", list(CONFIGURATION))
def test_an_unchanged_link_where_a_host_reads_configuration_keeps_a_blocking_limit(
    tmp_path: Path, case: str
) -> None:
    files, links, link = CONFIGURATION[case]
    repo = _repository(tmp_path, files, links)

    issues = host_audit_inventory(repo)["issues"]
    payload = _diff(repo)

    assert any(
        issue["kind"] == "unreadable" and issue["source"] == link and issue["blocking"]
        for issue in issues
    ), [(issue["kind"], issue["source"]) for issue in issues]
    assert payload["comparison_status"] == "incomparable"
    assert payload["rows"] == [] and payload["unchanged_limits"] == []
    assert not unread_link_unchanged(repo, "main", "HEAD", link)
    assert not unread_link_unchanged(repo, "main", None, link)


def test_the_comparator_never_names_a_link_at_a_configuration_location() -> None:
    """Defense in depth: whatever a caller's link proof answers, an `unreadable`
    limit at a configuration location refuses."""

    def inventory(source: str) -> dict:
        return {
            "issues": [{
                "issue_id": "i1", "kind": "unreadable", "host": "claude-code",
                "source": source, "blocking": True, "message": "m",
            }],
            "host_coverage": [{"host": "claude-code", "status": "partial", "sources_observed": []}],
            "grants": [], "artifacts": [],
        }

    def everything(_path: str) -> bool:
        return True

    for source in (".claude/agents", "pkg/.claude", ".github", "AGENTS.md", "x/.claude-plugin"):
        assert unchanged_limits(
            inventory(source), inventory(source), everything, unchanged_link=everything
        ) is None, source
    named = unchanged_limits(inventory(LINK), inventory(LINK), everything, unchanged_link=everything)
    assert named == [
        {"host": "claude-code", "limit": "unreadable", "source": LINK, "detail": UNCHANGED_LINK_DETAIL}
    ]
    # Without a link proof, `unreadable` refuses as it always has.
    assert unchanged_limits(inventory(LINK), inventory(LINK), everything) is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (LINK, False),
        ("docs/agent", False),
        ("pkg/policies", False),
        ("pkg/.cursor", False),
        (f"{LINK}/main.tf", False),
        ("", True),
        (".claude", True),
        (".claude/agents", True),
        (".claude/skills/review", True),
        ("pkg/.claude", True),
        ("pkg/.claude/hooks", True),
        (".codex", True),
        ("tools/.codex/config.toml", True),
        (".cursor/rules/x", True),
        (".vscode", True),
        (".github", True),
        (".github/workflows", True),
        (".github/actions/build", True),
        ("svc/.github", True),
        (".agents", True),
        (".agents/skills/x", True),
        (".agents-shipgate", True),
        (".shipgate", True),
        ("svc/.shipgate", True),
        ("policies", True),
        ("policies/vendor", True),
        ("AGENTS.md", True),
        ("CLAUDE.md", True),
        ("pkg/CLAUDE.md", True),
        ("pkg/AGENTS.override.md", True),
        (".mcp.json", True),
        ("pkg/.mcp.json", True),
        ("shipgate.yaml", True),
        ("plugins/demo/.claude-plugin", True),
        ("plugins/demo/.codex-plugin/plugin.json", True),
        ("plugins/demo/hooks/hooks.json", True),
        ("plugins/demo/security-hooks.json", True),
        ("PKG/.CLAUDE", True),
    ],
)
def test_which_paths_are_configuration_locations(path: str, expected: bool) -> None:
    assert is_host_configuration_location(path) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("../../modules", TARGET),
        ("../../modules/", TARGET),
        ("../.././modules", TARGET),
        ("../../modules/.", TARGET),
        ("../../modules//", None),
        ("", None),
        ("/abs/modules", None),
        ("..\\..\\modules", None),
        ("../../../../../modules", None),
        ("missing/../../../modules", None),
        ("../../modules/main.tf", None),
        ("..", None),
        ("../../../..", None),
        (".", None),
    ],
)
def test_the_named_directory_is_walked_one_component_at_a_time(text: str, expected: str | None) -> None:
    kinds = {
        ".happy": "tree", ".happy/terraform": "tree", TARGET: "tree", f"{TARGET}/main.tf": "blob",
        ".happy/terraform/envs": "tree", ".happy/terraform/envs/dev": "tree", LINK: "link",
    }

    assert _linked_directory(LINK, text, kinds.get) == expected


# --- a partial comparison (#808) names the link too --------------------------------

PLUGIN_MANIFEST = "plugins/demo/.claude-plugin/plugin.json"
PLUGIN_HOOKS = json.dumps(
    {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]}}
)


def test_a_partial_comparison_names_the_unchanged_link_beside_its_withheld_directory(
    tmp_path: Path,
) -> None:
    """A plugin directory whose edited manifest refuses is withheld (#808), and
    the rest must be proven unchanged: the link now is, so the comparison is
    `partial` with the MCP row, where it was refused outright."""

    files = {
        f"{TARGET}/main.tf": MODULE,
        PLUGIN_MANIFEST: "{not json",
        "plugins/demo/cfg/hooks.json": PLUGIN_HOOKS,
    }
    repo = _repository(tmp_path, files, None, head_files={PLUGIN_MANIFEST: "{still not json"})

    payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert _rows(payload) == MCP_ADDED
    assert _limits(payload) == _named()
    assert {
        item["scope"] for item in payload["coverage"]["items"] if item["status"] == "blocking_limit"
    } == {"plugins/demo"}


# --- #1154: an unchanged link is not an unread changed input -----------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "samples" / "simple_langchain_agent"
SETTINGS = ".claude/settings.json"
BASE_SETTINGS = '{"permissions": {"allow": ["Read"], "deny": ["Bash(rm:*)"]}}'
WIDENED = '{"permissions": {"allow": ["Read", "Bash(*)"], "deny": []}}'
WILDCARD = "SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW"


def _sample(tmp_path: Path, settings: str) -> Path:
    """The #1154 sample with the issue's Terraform link, unchanged; the head edits settings."""

    repo = tmp_path / "sample"
    shutil.copytree(SAMPLE, repo, ignore=shutil.ignore_patterns("expected"))
    _write(repo, ".gitignore", "agents-shipgate-reports/\n.agents-shipgate/\n")
    _write(repo, f"{TARGET}/main.tf", MODULE)
    _link(repo, LINK, TEXT)
    _write(repo, SETTINGS, BASE_SETTINGS)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "change")
    _write(repo, SETTINGS, settings)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "head")
    return repo


def _verify_strict(repo: Path) -> tuple[dict, int]:
    """`verify` of the checked-out head. A configured `--head` archives the
    whole commit, and that archive still refuses any link (#688), so the
    working tree is what a configured run can read here."""

    result = CliRunner().invoke(
        app,
        [
            "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--base", "main",
            "--ci-mode", "strict", "--json",
        ],
        env=_CLI_ENV,
    )
    return json.loads(result.output), result.exit_code


def _unread_gaps(verify: dict) -> list[str]:
    return [
        gap["source_ref"]
        for gap in verify["release_decision"]["evidence_coverage"]["evidence_gaps"]
        if gap.get("source_type") == "agent_boundary_input"
    ]


def test_an_unchanged_link_is_not_an_unread_changed_input(tmp_path: Path) -> None:
    """#1154 holds the verdict on a changed host file the reader could not
    read. The link is unread but unchanged, so it adds no gap: a readable
    widening is still `blocked` on its own row."""

    repo = _sample(tmp_path, WIDENED)

    verify, exit_code = _verify_strict(repo)

    assert exit_code == 20
    assert verify["decision"] == "blocked"
    assert WILDCARD in {item["check_id"] for item in verify["release_decision"]["blockers"]}
    assert _unread_gaps(verify) == []


def test_an_unreadable_changed_file_beside_an_unchanged_link_gets_its_own_gap_only(
    tmp_path: Path,
) -> None:
    repo = _sample(tmp_path, "// widened\n" + WIDENED)

    verify, exit_code = _verify_strict(repo)

    assert exit_code == 20
    assert verify["decision"] == "insufficient_evidence"
    assert _unread_gaps(verify) == [SETTINGS]
