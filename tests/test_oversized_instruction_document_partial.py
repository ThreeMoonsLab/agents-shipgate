"""#973: a plain instruction document past the size bound no longer hides the rest.

saleor/storefront#1252 adds `.mcp.json` and `.cursor/mcp.json`, each launching
`next-devtools-mcp@latest`. The same pull request regenerates
`skills/saleor-paper-storefront/AGENTS.md`, a 346,349-byte compiled document
(blob `aa159c2` -> `cf87eaa`), which is past the instruction classifier's
256 KiB bound on both sides. `diff`, `verify` and the PR comment refused the
whole comparison (`base_inventory_incomplete; head_inventory_incomplete`) with
no row, and the refusal named the file only as "unsupported in base and head",
which was read as an unchanged file. An unchanged one has been named in
`unchanged_limits` since #721; this one changed.

A plain instruction document (`AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md`)
read within the bound is `guidance` with one digest whatever it says, and the
reader publishes no grant for it, so no other source's grant can depend on its
text. Read directly on both sides and refused only by that bound, it is now
left uncompared on both sides and named as the `scope` of a `partial`
comparison, and the rest is compared. Every other shape still refuses: a
skill, a command or a Cursor rule (which declare grants or activation), an
unread role, a document on one side only, a NUL byte, a document reached
through a link, and any other limit the change touched. A limit both sides
carry now says whether the file changed in this change.

Authority does not move: `check` names no scope and refuses as before, and
`verify`'s control state and the control envelope read a partial comparison
as incomplete.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.instruction_structure import MAX_INSTRUCTION_BYTES
from agents_shipgate.report.host_comparison import partial_scope_lines
from agents_shipgate.schemas.host_comparison import (
    BLOCKING_SOURCE_IDENTITY_NOTES,
    HostComparison,
)

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = "skills/demo/AGENTS.md"
HOSTS = ["claude-code", "codex", "cursor"]
#: One line past the bound, as the real document is: text a host reads as prose.
LONG = "# Rules\n" + "A rule a person reads offline.\n" * (MAX_INSTRUCTION_BYTES // 31 + 1)
assert len(LONG.encode()) > MAX_INSTRUCTION_BYTES
LONGER = LONG + "## One more rule\n"
MCP = {"mcpServers": {"next-devtools": {"command": "npx", "args": ["-y", "next-devtools-mcp@latest"]}}}
#: The pull request's own additions.
SALEOR_HEAD = {".mcp.json": MCP, ".cursor/mcp.json": MCP}
MCP_ROWS = [
    ("claude-code .mcp.json", "added"),
    ("cursor .cursor/mcp.json", "added"),
]
BOTH = ["base_inventory_incomplete", "head_inventory_incomplete"]
CHANGED = BLOCKING_SOURCE_IDENTITY_NOTES[False]
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
_LINKS = pytest.mark.skipif(os.name == "nt", reason="symbolic link fixtures")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _write(repo: Path, name: str, value: object) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "-m", message)


def _repository(
    tmp_path: Path,
    base: dict[str, object],
    head: dict[str, object],
    *,
    removed: tuple[str, ...] = (),
    links: dict[str, str] | None = None,
) -> Path:
    """`main` holds ``base`` and ``links``; branch `change` commits ``head`` without ``removed``."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, value in {"README.md": "# demo\n", **base}.items():
        _write(repo, name, value)
    for name, target in (links or {}).items():
        os.symlink(target, repo / name)
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "change")
    for name, value in head.items():
        _write(repo, name, value)
    for name in removed:
        (repo / name).unlink()
    _commit(repo, "change")
    return repo


def _invoke(args: list[str]) -> str:
    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1", "COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return result.output


def _diff(repo: Path) -> tuple[str, dict]:
    command = ["diff", "--workspace", str(repo), "--base", "main"]
    return _invoke(command), json.loads(_invoke([*command, "--json"]))


def _all_routes(repo: Path, tmp_path: Path) -> tuple[str, dict, str, str]:
    """diff text and JSON, and verify's comparison, PR comment and text, agreeing."""

    text, payload = _diff(repo)
    out = tmp_path / "out"
    verify_text = _invoke([
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--format", "text", "--base", "main",
        "--head", _git(repo, "rev-parse", "HEAD"),
    ])
    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    comparison = verifier["host_comparison"]
    for key in ("comparison_status", "incomparable_reasons", "rows", "unchanged_limits", "coverage"):
        assert comparison[key] == payload[key], key
    if payload["review"] is not None:
        assert comparison["review"]["changes"] == payload["review"]["changes"]
    Draft202012Validator(
        json.loads((ROOT / "docs/verifier-schema.v0.21.json").read_text(encoding="utf-8"))
    ).validate(verifier)
    return text, payload, (out / "pr-comment.md").read_text(encoding="utf-8"), verify_text


def _rows(payload: dict) -> list[tuple[str, str]]:
    return sorted((row["subject"], row["direction"]) for row in payload["rows"])


def _limits(payload: dict) -> list[tuple]:
    return sorted(
        (item["source"], item["limit"], item["side"], item["scope"])
        for item in payload["coverage"]["items"]
        if item["status"] == "blocking_limit"
    )


def _refused(payload: dict, reasons: list[str]) -> None:
    """Exactly the refusal published before #973: no row, no review, no scope."""

    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == reasons
    assert payload["rows"] == [] and payload["review"] is None
    assert all(item["scope"] is None for item in payload["coverage"]["items"])


# --- the issue's shape ------------------------------------------------------------


def test_the_mcp_rows_survive_a_changed_oversized_agents_md(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {DOCUMENT: LONG}, {DOCUMENT: LONGER, **SALEOR_HEAD})

    text, payload, comment, verify_text = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "partial"
    # The refusal's reasons are still what they were: neither inventory is complete.
    assert payload["incomparable_reasons"] == BOTH
    assert _rows(payload) == MCP_ROWS
    assert payload["unchanged_limits"] == []
    assert _limits(payload) == [(DOCUMENT, "unsupported", "both", DOCUMENT)]
    [limit] = [item for item in payload["coverage"]["items"] if item["status"] == "blocking_limit"]
    assert limit["hosts"] == HOSTS
    assert "instruction_text_limit" in limit["detail"]
    assert limit["detail"].endswith(CHANGED)
    assert payload["review"]["summary"] == {"rows": 2, "changes": 2, "widenings": 2}

    lines = text.splitlines()
    assert lines[0].startswith("Partial comparison against main (")
    assert lines[0].endswith("-> working tree: base_inventory_incomplete; head_inventory_incomplete")
    assert lines[1] == (
        f"Not compared: {DOCUMENT}, an instruction document longer than this entry reads "
        "(a kind it treats as guidance, which declares no grant it compares), so a change "
        "to it is not shown and nothing is claimed about it."
    )
    assert lines[2].startswith("The changes below come only from other sources, so they are not")
    item_line = (
        f"  {DOCUMENT} ({', '.join(HOSTS)}): unsupported in base and head (longer than this "
        "entry reads), so this document was not compared; it changed in this change"
    )
    assert item_line in lines
    assert "Cannot compare" not in text and "No static host-grant changes detected" not in text
    assert "next-devtools-mcp@latest" in text

    assert verify_text.splitlines()[:2] == [
        "Host capability comparison partial: base_inventory_incomplete; head_inventory_incomplete",
        lines[1],
    ]
    assert f"Not compared: ` {DOCUMENT} `, an instruction document" in comment
    assert "so this document was not compared; it changed in this change" in comment
    assert "next-devtools-mcp@latest" in comment


def test_the_unchanged_case_keeps_its_shipped_contract(tmp_path: Path) -> None:
    """#721 already named an unchanged one; that answer is `comparable` and stays so."""

    repo = _repository(tmp_path, {DOCUMENT: LONG}, SALEOR_HEAD)

    text, payload, _comment, _verify_text = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "comparable"
    assert payload["incomparable_reasons"] == []
    assert _rows(payload) == MCP_ROWS
    assert sorted((limit["host"], limit["source"], limit["limit"]) for limit in payload["unchanged_limits"]) == [
        (host, DOCUMENT, "unsupported") for host in HOSTS
    ]
    assert _limits(payload) == []
    assert "Not compared: unchanged in this change and not read" in text


def test_no_row_beside_a_changed_document_is_never_a_no_change_answer(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path, {DOCUMENT: LONG, ".mcp.json": MCP}, {DOCUMENT: LONGER}
    )

    text, payload, comment, verify_text = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "partial"
    assert payload["rows"] == []
    for output in (text, comment, verify_text):
        assert "No static host-grant changes detected" not in output
        assert "That is not a no-change answer for this change" in output
    assert "No static host-grant change was detected outside it." in text


def test_two_documents_and_an_unchanged_limit_of_the_rest(tmp_path: Path) -> None:
    """Each changed document is its own scope; an unchanged limit elsewhere is
    still named in `unchanged_limits`, as on a comparable comparison."""

    other = "docs/CLAUDE.md"
    skill = ".claude/skills/review/SKILL.md"
    unresolved = "---\nname: review\ndescription: d\neffort: extreme\n---\nbody\n"
    repo = _repository(
        tmp_path,
        {DOCUMENT: LONG, other: LONG, skill: unresolved},
        {DOCUMENT: LONGER, other: LONGER, **SALEOR_HEAD},
    )

    text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert _limits(payload) == [
        (other, "unsupported", "both", other),
        (DOCUMENT, "unsupported", "both", DOCUMENT),
    ]
    assert {limit["source"] for limit in payload["unchanged_limits"]} == {skill}
    assert _rows(payload) == MCP_ROWS
    assert text.splitlines()[1].startswith(
        f"Not compared: {other}, {DOCUMENT}, instruction documents longer than this entry reads"
    )


def test_a_broken_plugin_beside_a_changed_document_withholds_both(tmp_path: Path) -> None:
    manifest = "plugins/demo/.claude-plugin/plugin.json"
    hooks = "plugins/demo/cfg/hooks.json"
    hook = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo one"}]}]}}
    repo = _repository(
        tmp_path,
        {DOCUMENT: LONG, manifest: {"name": "demo", "hooks": "./cfg/hooks.json"}, hooks: hook},
        {DOCUMENT: LONGER, manifest: "{not json", **SALEOR_HEAD},
    )

    text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert _limits(payload) == [
        (manifest, "parse_failed", "head", "plugins/demo"),
        (DOCUMENT, "unsupported", "both", DOCUMENT),
    ]
    assert _rows(payload) == MCP_ROWS
    lines = text.splitlines()
    assert lines[1].startswith("Not compared: plugins/demo, a plugin directory")
    assert lines[2].startswith(f"Nor {DOCUMENT}, an instruction document longer than this entry reads")


# --- independence not established: refused exactly as before ---------------------

SKILL_FRONTMATTER = "---\nname: demo\ndescription: d\nallowed-tools: Bash(*)\n---\n"
CURSOR_FRONTMATTER = "---\ndescription: d\nalwaysApply: true\n---\n"

NOT_INDEPENDENT = {
    # A skill can declare `allowed-tools` and `hooks`: what it grants is
    # exactly what an unread one would hide.
    "changed-oversized-skill": (
        {".claude/skills/demo/SKILL.md": SKILL_FRONTMATTER + LONG},
        {".claude/skills/demo/SKILL.md": SKILL_FRONTMATTER + LONGER, **SALEOR_HEAD},
        (), BOTH,
    ),
    # A command at a familiar name is still a command.
    "changed-oversized-command-named-agents-md": (
        {".claude/commands/AGENTS.md": SKILL_FRONTMATTER + LONG},
        {".claude/commands/AGENTS.md": SKILL_FRONTMATTER + LONGER, **SALEOR_HEAD},
        (), BOTH,
    ),
    # A Cursor rule declares when it applies.
    "changed-oversized-cursor-rule": (
        {".cursor/rules/demo.mdc": CURSOR_FRONTMATTER + LONG},
        {".cursor/rules/demo.mdc": CURSOR_FRONTMATTER + LONGER, **SALEOR_HEAD},
        (), BOTH,
    ),
    # A role this entry does not read is unread whatever its size.
    "changed-unread-role-named-claude-md": (
        {".claude/agents/CLAUDE.md": "short\n"},
        {".claude/agents/CLAUDE.md": "short, edited\n", **SALEOR_HEAD},
        (), BOTH,
    ),
    # Past the bound on one side only: the other side read it.
    "grew-past-the-bound": (
        {DOCUMENT: "# Rules\n"}, {DOCUMENT: LONG, **SALEOR_HEAD}, (), ["head_inventory_incomplete"],
    ),
    "shrank-within-the-bound": (
        {DOCUMENT: LONG}, {DOCUMENT: "# Rules\n", **SALEOR_HEAD}, (), ["base_inventory_incomplete"],
    ),
    "added": ({}, {DOCUMENT: LONG, **SALEOR_HEAD}, (), ["head_inventory_incomplete"]),
    "removed": ({DOCUMENT: LONG}, SALEOR_HEAD, (DOCUMENT,), ["base_inventory_incomplete"]),
    # Not readable prose: a NUL byte is not a long document.
    "nul-byte": (
        {DOCUMENT: "# Rules\x00\n"}, {DOCUMENT: "# Rules\x00 edited\n", **SALEOR_HEAD}, (), BOTH,
    ),
    # Another limit the change touched still refuses.
    "another-changed-limit": (
        {DOCUMENT: LONG},
        {DOCUMENT: LONGER, ".mcp.json": "{not json", ".cursor/mcp.json": MCP},
        (), BOTH,
    ),
    # Nothing beside the document was read, so nothing would be retained.
    "nothing-else-read": ({DOCUMENT: LONG}, {DOCUMENT: LONGER}, (), BOTH),
}


@pytest.mark.parametrize("case", list(NOT_INDEPENDENT))
def test_rows_are_withheld_where_independence_is_not_established(tmp_path: Path, case: str) -> None:
    base, head, removed, reasons = NOT_INDEPENDENT[case]
    repo = _repository(tmp_path, base, head, removed=removed)

    text, payload = _diff(repo)

    _refused(payload, reasons)
    assert text.startswith(f"Cannot compare against main: {'; '.join(reasons)}\n")
    assert "Partial comparison" not in text and "Not compared:" not in text


@_LINKS
def test_a_document_read_through_a_link_still_refuses(tmp_path: Path) -> None:
    """`CLAUDE.md -> AGENTS.md`: the linked read is not withheld (#700, #822),
    so the comparison refuses although the file it lands on alone would not."""

    repo = _repository(
        tmp_path, {"AGENTS.md": LONG}, {"AGENTS.md": LONGER, **SALEOR_HEAD},
        links={"CLAUDE.md": "AGENTS.md"},
    )

    _text, payload = _diff(repo)

    _refused(payload, BOTH)


def test_a_refusal_says_the_limited_file_changed(tmp_path: Path) -> None:
    """The wording half of #973, on a refusal: a skill past the bound that the
    change edited is named as changed, never left to read as unchanged."""

    skill = ".claude/skills/demo/SKILL.md"
    repo = _repository(
        tmp_path,
        {skill: SKILL_FRONTMATTER + LONG},
        {skill: SKILL_FRONTMATTER + LONGER, **SALEOR_HEAD},
    )

    text, payload, comment, verify_text = _all_routes(repo, tmp_path)

    _refused(payload, BOTH)
    [item] = [item for item in payload["coverage"]["items"] if item["status"] == "blocking_limit"]
    assert item["detail"].endswith(CHANGED)
    line = (
        f"{skill} (claude-code): unsupported in base and head, so neither inventory is "
        "complete; it changed in this change"
    )
    assert f"  {line}" in text.splitlines()
    assert line in verify_text
    assert f"- ` {skill} ` (claude-code): unsupported in base and head, so neither inventory" in comment
    assert "it changed in this change" in comment


# --- authority is unchanged -----------------------------------------------------


@pytest.mark.parametrize("fmt", ["agent-boundary-json", "agent-control-json"])
def test_check_refuses_exactly_as_before(tmp_path: Path, fmt: str) -> None:
    repo = _repository(tmp_path, {DOCUMENT: LONG}, {DOCUMENT: LONGER, **SALEOR_HEAD})

    payload = json.loads(_invoke([
        "check", "--agent", "claude-code", "--workspace", str(repo), "--base", "main",
        "--head", "HEAD", "--format", fmt,
    ]))

    assert payload["decision"] != "allow"
    block = payload if fmt == "agent-boundary-json" else payload["capability_rows"]
    if fmt == "agent-control-json":
        assert payload["control_state"] != "complete"
    assert block["comparison_status"] == "incomparable"
    assert block["incomparable_reasons"] == BOTH
    assert block["rows"] == []


def test_verify_control_is_the_incomplete_comparisons(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {DOCUMENT: LONG}, {DOCUMENT: LONGER, **SALEOR_HEAD})
    args = ("verify", "--preview", "--workspace", str(repo), "--base", "main", "--head", "HEAD")

    verifier = json.loads(_invoke([*args, "--json"]))
    envelope = json.loads(_invoke([*args, "--format", "control"]))

    assert verifier["host_comparison"]["comparison_status"] == "partial"
    control = verifier["control"]
    assert control["state"] == "agent_action_required"
    assert not any(control["permissions"].values())
    assert verifier["merge_verdict"] == "unknown" and not verifier["can_merge_without_human"]
    assert envelope["capability_rows"] == {
        "comparison_status": "incomparable",
        "incomparable_reasons": BOTH,
        "rows": [],
        "omitted_rows": 0,
        "unchanged_limit_count": 0,
    }


# --- the published shape -----------------------------------------------------------


def _item(**overrides) -> dict:
    return {
        "source": DOCUMENT, "hosts": ["codex"], "side": "both", "status": "blocking_limit",
        "limit": "unsupported", "detail": f"d {CHANGED}", "scope": DOCUMENT, **overrides,
    }


def test_a_document_scope_and_a_script_scope_read_apart() -> None:
    """Both name their own path as `scope`; the limit kind tells them apart, so
    a document is never described as a hook script whose hook was compared."""

    script = "hooks/guard.sh"
    comparison = HostComparison.model_validate({
        "comparison_status": "partial",
        "incomparable_reasons": BOTH,
        "head_kind": "worktree",
        "coverage": {"items": [
            _item(),
            _item(source=script, scope=script, limit="unreadable", detail="d"),
        ]},
    })

    lines = partial_scope_lines(comparison)

    assert lines[0].startswith(f"Not compared: {DOCUMENT}, an instruction document")
    assert lines[1] == f"Nor the bytes of {script}, hook scripts this entry could not read on both sides alike."


def test_an_unchanged_document_beside_a_broken_plugin_stays_an_unchanged_limit(tmp_path: Path) -> None:
    """Retention never turns a document #721 proves unchanged into a scope: it
    is named in `unchanged_limits` exactly as before."""

    manifest = "plugins/demo/.claude-plugin/plugin.json"
    hooks = "plugins/demo/cfg/hooks.json"
    hook = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo one"}]}]}}
    repo = _repository(
        tmp_path,
        {DOCUMENT: LONG, manifest: {"name": "demo", "hooks": "./cfg/hooks.json"}, hooks: hook},
        {manifest: "{not json", **SALEOR_HEAD},
    )

    _text, payload = _diff(repo)

    assert payload["comparison_status"] == "partial"
    assert _limits(payload) == [(manifest, "parse_failed", "head", "plugins/demo")]
    assert {limit["source"] for limit in payload["unchanged_limits"]} == {DOCUMENT}
    assert _rows(payload) == MCP_ROWS


def test_a_document_a_hook_runs_as_its_script_still_refuses(tmp_path: Path) -> None:
    """Its bytes are an input of a hook grant (#702), so a row outside it
    depends on it."""

    settings = {"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": f'"${{CLAUDE_PROJECT_DIR}}/{DOCUMENT}"'},
    ]}]}}
    repo = _repository(
        tmp_path,
        {DOCUMENT: LONG, ".claude/settings.json": settings},
        {DOCUMENT: LONGER, **SALEOR_HEAD},
    )

    _text, payload = _diff(repo)

    assert payload["comparison_status"] == "incomparable"
    assert payload["rows"] == []
