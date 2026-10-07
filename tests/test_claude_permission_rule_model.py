"""Host series 1: the Claude Code permission-rule model, one table for every route.

Each case is a two-commit repository whose Claude Code settings change, read
by every route that projects the host comparison: `diff` text and `--json`,
`check`, `verify`'s `host_comparison` and `audit --host --drift` against a
baseline saved at the base commit. The table pins, per case, the rows and
which of them widen, the exact widening count the text prints, `check`'s
decision and violations, and drift's expansion signals.

Every reading follows Claude Code's permissions page,
https://code.claude.com/docs/en/permissions (read 2026-10-06):

* #918 — "Wildcard patterns": a trailing `:*` is a trailing ` *`, and the
  space is part of the rule, so `pnpm run lint *` does not cover
  `pnpm run lint:fix *`. A broad allow replaced by proven subsets narrows.
* #941 — an added allow already matched by an allow rule the same source
  declared at the base adds nothing.
* #969 — "Read and Edit": `Edit(**/.env.example)` names `.env.example` files,
  not every file; under a bare `Edit` it adds nothing.
* #974 — "Read and Edit": a `!` deny or ask pattern carves paths out of the
  `path` or `./path` rules listed *before* it in the same source; listed
  first it carves nothing, and it cannot reach `/`, `~/` or `//` rules.
* #938 — "Read and Edit": a path-scoped `Write(...)` rule is accepted and
  never consulted; a bare `Write` rule is matched at the tool level.

The controls keep their markers: a genuinely new rule, a deny→allow move, a
broadened pattern, and every pair the model cannot decide (shell syntax, an
exec wrapper, a `**` path, a skipped unanchored `*`, another tool's name).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}
SETTINGS = ".claude/settings.json"
LOCAL = ".claude/settings.local.json"

ALLOW_EXPANDED = "SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED"
DENY_REMOVED = "SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _invoke(args: list[str]) -> str:
    return CliRunner().invoke(app, args).output


def _write(repo: Path, files: dict[str, dict | None]) -> None:
    for name, data in files.items():
        path = repo / name
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def _repository(tmp_path: Path, base: dict[str, dict | None], head: dict[str, dict | None]) -> Path:
    """A base commit with a saved host-grants baseline, then the edit, as two commits.

    `add -f`: a machine-wide ignore file may hide `settings.local.json`.
    """

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n.agents-shipgate/\n", encoding="utf-8")
    _write(repo, base)
    _git(repo, "add", "-A")
    _git(repo, "add", "-f", "--", *[name for name, data in base.items() if data is not None])
    _git(repo, "commit", "-q", "-m", "base")
    _invoke(["audit", "--host", "--workspace", str(repo), "--save-baseline", "--json"])
    _git(repo, "checkout", "-q", "-b", "change")
    _write(repo, head)
    _git(repo, "add", "-A")
    present = [name for name, data in head.items() if data is not None]
    if present:
        _git(repo, "add", "-f", "--", *present)
    _git(repo, "commit", "-q", "-m", "head")
    return repo


def _routes(repo: Path) -> dict:
    workspace = ["--workspace", str(repo), "--base", "main"]
    return {
        "text": _invoke(["diff", *workspace]),
        "diff": json.loads(_invoke(["diff", *workspace, "--json"])),
        "check": json.loads(
            _invoke(["check", "--agent", "claude-code", *workspace, "--format", "agent-boundary-json"])
        ),
        "verify": json.loads(
            _invoke(["verify", "--preview", *workspace, "--head", "HEAD", "--json"])
        )["host_comparison"],
        "drift": json.loads(
            _invoke(["audit", "--host", "--workspace", str(repo), "--drift", "--json"])
        ),
    }


def _marked(rows: list[dict]) -> list[tuple[str, str, bool]]:
    """Every row as (direction, cell, expands), in a route-independent order."""

    return sorted(
        (
            row["direction"],
            f"{row['disposition']}: " + (row["after"] if row["after"] != "—" else row["before"]),
            row["expands"],
        )
        for row in rows
    )


def _shape(rows: list[dict]) -> list[tuple[str, bool]]:
    """`check` redacts rule arguments, so it is compared by shape."""

    return sorted((row["direction"], row["expands"]) for row in rows)


def _settings(allow=(), deny=(), ask=()) -> dict:
    permissions = {
        key: list(value) for key, value in (("allow", allow), ("deny", deny), ("ask", ask)) if value
    }
    return {"permissions": permissions}


@dataclass(frozen=True)
class Case:
    """One fixture and what every route must show for it.

    ``rows`` is (direction, "disposition: rule", expands) for every published
    row; the widening count is the number marked. ``changes`` is what the text
    counts once it joins a replacement, respelling or move. ``base``/``head``
    map each settings file to its content (``None``: absent).
    """

    base: dict[str, dict | None]
    head: dict[str, dict | None]
    rows: list[tuple[str, str, bool]]
    changes: int
    decision: str
    violations: list[str]
    signals: list[str]
    why: dict[str, str] = field(default_factory=dict)
    #: The ⚠ count the text prints, where it joins rows (a move): else ``widenings``.
    joined_widenings: int | None = None

    @property
    def widenings(self) -> int:
        """The widening markers: published rows with ``expands: true``."""

        return sum(expands for *_, expands in self.rows)

    @property
    def text_widenings(self) -> int:
        return self.widenings if self.joined_widenings is None else self.joined_widenings


def _one(base: dict, head: dict, **expected) -> Case:
    return Case(base={SETTINGS: base}, head={SETTINGS: head}, **expected)


_LINT = ("Bash(pnpm run lint:fix *)", "Bash(pnpm run lint:eslint *)", "Bash(pnpm run lint:prettier *)")
_GH = ("Bash(gh pr view *)", "Bash(gh pr diff *)", "Bash(gh pr list *)", "Bash(gh pr checks *)")
_MERGES = (
    "Bash(git merge -X ours:*)", "Bash(git merge --no-ff -X ours:*)",
    "Bash(git merge --no-ff -q -X ours:*)",
)

CASES: dict[str, Case] = {
    # ---- #918: the issue's minimal reproduction, exactly three widenings.
    "918_minimal": _one(
        _settings(allow=["Bash(git add:*)", "Bash(gh pr *)", "Bash(pnpm run lint:*)"]),
        _settings(allow=["Bash(git add *)", *_GH, "Bash(pnpm run lint *)", *_LINT]),
        rows=sorted([
            ("added", "allow: Bash(git add *)", False),
            *[("added", f"allow: {rule}", False) for rule in _GH],
            ("added", "allow: Bash(pnpm run lint *)", False),
            *[("added", f"allow: {rule}", True) for rule in _LINT],
            ("removed", "allow: Bash(git add:*)", False),
            ("removed", "allow: Bash(gh pr *)", False),
            ("removed", "allow: Bash(pnpm run lint:*)", False),
        ]),
        changes=10,
        decision="require_review",
        violations=[ALLOW_EXPANDED],
        signals=sorted(f"allow_rule_added: claude-code:{rule}" for rule in _LINT),
    ),
    "918_equivalent_spelling_only": _one(
        _settings(allow=["Bash(git add:*)"]),
        _settings(allow=["Bash(git add *)"]),
        rows=[("added", "allow: Bash(git add *)", False), ("removed", "allow: Bash(git add:*)", False)],
        changes=1, decision="allow", violations=[], signals=[],
    ),
    "918_bare_tool_spelling": _one(
        _settings(allow=["Bash(*)"]),
        _settings(allow=["Bash"]),
        rows=[("added", "allow: Bash", False), ("removed", "allow: Bash(*)", False)],
        changes=1, decision="allow", violations=[], signals=[],
    ),
    "918_one_to_many_narrowing": _one(
        _settings(allow=["Bash(gh pr *)"]),
        _settings(allow=list(_GH)),
        rows=sorted([
            *[("added", f"allow: {rule}", False) for rule in _GH],
            ("removed", "allow: Bash(gh pr *)", False),
        ]),
        changes=5, decision="allow", violations=[], signals=[],
        why={
            "Bash(gh pr *)": (
                "removes this allow rule; it is narrowed to 4 added rule(s) in this source "
                "that match only part of what it matched: allow: Bash(gh pr checks *), "
                "allow: Bash(gh pr diff *), allow: Bash(gh pr list *), allow: Bash(gh pr view *)"
            ),
            "Bash(gh pr view *)": (
                "runs without a prompt, but adds nothing: allow: Bash(gh pr *), declared in "
                "this source at the base, already matches everything it matches"
            ),
        },
    ),
    "918_word_boundary": _one(
        _settings(allow=["Bash(pnpm run lint:*)"]),
        _settings(allow=["Bash(pnpm run lint *)", "Bash(pnpm run lint:fix *)"]),
        rows=[
            ("added", "allow: Bash(pnpm run lint *)", False),
            ("added", "allow: Bash(pnpm run lint:fix *)", True),
            ("removed", "allow: Bash(pnpm run lint:*)", False),
        ],
        changes=2, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=["allow_rule_added: claude-code:Bash(pnpm run lint:fix *)"],
    ),
    "918_deny_respelled": _one(
        _settings(deny=["Bash(rm:*)"]),
        _settings(deny=["Bash(rm *)"]),
        rows=[("added", "deny: Bash(rm *)", False), ("removed", "deny: Bash(rm:*)", False)],
        changes=1, decision="allow", violations=[], signals=[],
    ),
    # ---- #941: an unchanged broad allow already covers the additions.
    "941_minimal": _one(
        _settings(allow=["Bash(git *)"]),
        _settings(allow=["Bash(git *)", "Bash(git merge --no-ff -X ours:*)"]),
        rows=[("added", "allow: Bash(git merge --no-ff -X ours:*)", False)],
        changes=1, decision="allow", violations=[], signals=[],
        why={
            "Bash(git merge --no-ff -X ours:*)": (
                "runs without a prompt, but adds nothing: allow: Bash(git *), declared in "
                "this source at the base, already matches everything it matches"
            ),
        },
    ),
    "941_xollama_pr5_shape": _one(
        _settings(allow=["Bash(git *)", "Bash(npm run build)"]),
        _settings(allow=["Bash(git *)", "Bash(npm run build)", *_MERGES]),
        rows=sorted(("added", f"allow: {rule}", False) for rule in _MERGES),
        changes=3, decision="allow", violations=[], signals=[],
    ),
    # ---- #969: a filename-scoped glob is not the whole tool.
    "969_minimal": _one(
        _settings(allow=["Edit", "Write"]),
        _settings(allow=["Edit", "Write", "Edit(**/.env.example)", "Write(**/.env.example)"]),
        rows=[
            ("added", "allow: Edit(**/.env.example)", False),
            ("added", "allow: Write(**/.env.example)", False),
        ],
        changes=2, decision="allow", violations=[], signals=[],
    ),
    "969_odysseus_pr3_shape": _one(
        _settings(allow=["Edit", "Write"], deny=["Read(.env)", "Read(.env.*)"]),
        _settings(allow=["Edit", "Write", "Edit(**/.env.example)", "Write(**/.env.example)"]),
        rows=[
            ("added", "allow: Edit(**/.env.example)", False),
            ("added", "allow: Write(**/.env.example)", False),
            ("removed", "deny: Read(.env)", True),
            ("removed", "deny: Read(.env.*)", True),
        ],
        changes=4, decision="require_review", violations=[DENY_REMOVED],
        signals=[
            "deny_rule_removed: claude-code:Read(.env)",
            "deny_rule_removed: claude-code:Read(.env.*)",
        ],
    ),
    "969_scoped_glob_without_bare_tool": _one(
        _settings(allow=["Read"]),
        _settings(allow=["Read", "Edit(**/.env.example)"]),
        rows=[("added", "allow: Edit(**/.env.example)", True)],
        changes=1, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=["allow_rule_added: claude-code:Edit(**/.env.example)"],
        why={"Edit(**/.env.example)": "runs without a prompt"},
    ),
    # ---- #974: ordered `!` carve-outs.
    "974_uoacs_pr504_shape": _one(
        _settings(allow=["Bash(npm run *)"]),
        _settings(
            allow=["Bash(npm run *)"],
            deny=[
                "Read(.env)", "Read(.env.*)", "Read(!.env.example)",
                "Edit(.env)", "Edit(.env.*)", "Edit(!.env.example)",
            ],
        ),
        rows=sorted(
            ("added", f"deny: {rule}", False)
            for rule in (
                "Read(.env)", "Read(.env.*)", "Read(!.env.example)",
                "Edit(.env)", "Edit(.env.*)", "Edit(!.env.example)",
            )
        ),
        changes=6, decision="allow", violations=[], signals=[],
        why={
            "Read(!.env.example)": (
                "a carve-out: paths it matches are excepted from the earlier deny rules it "
                "follows in this source (deny: Read(.env), deny: Read(.env.*)); it lifts no "
                "denial the base declared"
            ),
        },
    ),
    "974_exception_after_existing_deny": _one(
        _settings(deny=["Read(.env.*)"]),
        _settings(deny=["Read(.env.*)", "Read(!.env.example)"]),
        rows=[("added", "deny: Read(!.env.example)", True)],
        changes=1, decision="require_review", violations=[DENY_REMOVED],
        signals=["deny_carve_out_added: claude-code:Read(!.env.example)"],
    ),
    "974_exception_before_existing_deny": _one(
        _settings(deny=["Read(.env.*)"]),
        _settings(deny=["Read(!.env.example)", "Read(.env.*)"]),
        rows=[("added", "deny: Read(!.env.example)", False)],
        changes=1, decision="allow", violations=[], signals=[],
        why={
            "Read(!.env.example)": (
                "a carve-out listed before every deny rule it could except paths from in "
                "this source, so it excepts nothing"
            ),
        },
    ),
    "974_ordering_only_now_effective": _one(
        _settings(deny=["Read(!.env.example)", "Read(.env.*)"]),
        _settings(deny=["Read(.env.*)", "Read(!.env.example)"]),
        rows=[("widened", "deny: Read(!.env.example)", True)],
        changes=1, decision="require_review", violations=[DENY_REMOVED],
        signals=["deny_carve_out_changed: claude-code:Read(!.env.example)"],
    ),
    "974_ordering_only_now_ineffective": _one(
        _settings(deny=["Read(.env.*)", "Read(!.env.example)"]),
        _settings(deny=["Read(!.env.example)", "Read(.env.*)"]),
        rows=[("changed", "deny: Read(!.env.example)", False)],
        changes=1, decision="allow", violations=[], signals=[],
    ),
    "974_exception_in_another_source": Case(
        base={SETTINGS: _settings(deny=["Read(.env.*)"]), LOCAL: None},
        head={SETTINGS: _settings(deny=["Read(.env.*)"]), LOCAL: _settings(deny=["Read(!.env.example)"])},
        rows=[("added", "deny: Read(!.env.example)", False)],
        changes=1, decision="allow", violations=[], signals=[],
    ),
    "974_anchored_rule_not_reopened": _one(
        _settings(deny=["Read(/.env.example)"]),
        _settings(deny=["Read(/.env.example)", "Read(!.env.example)"]),
        rows=[("added", "deny: Read(!.env.example)", False)],
        changes=1, decision="allow", violations=[], signals=[],
    ),
    "974_ask_exception_after_existing_ask": _one(
        _settings(ask=["Edit(src/**)"]),
        _settings(ask=["Edit(src/**)", "Edit(!src/generated/**)"]),
        rows=[("added", "ask: Edit(!src/generated/**)", True)],
        # `check` raises no rule for an ask list, as for a removed ask rule; the
        # change is not a narrowing, so the protected file goes to a human.
        changes=1, decision="require_review",
        violations=["SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"],
        signals=["ask_carve_out_added: claude-code:Edit(!src/generated/**)"],
    ),
    "974_exception_removed": _one(
        _settings(deny=["Read(.env.*)", "Read(!.env.example)"]),
        _settings(deny=["Read(.env.*)"]),
        rows=[("removed", "deny: Read(!.env.example)", False)],
        changes=1, decision="allow", violations=[], signals=[],
        why={
            "Read(!.env.example)": (
                "removes a carve-out; the paths it excepted from deny: Read(.env.*) are denied again"
            ),
        },
    ),
    # ---- #938: path-scoped Write rules are not consulted; bare Write is.
    "938_design_iq_pr218_shape": _one(
        _settings(deny=[
            "Read(.env)", "Edit(.env)", "Write(.env)",
            "Read(.env.local)", "Edit(.env.local)", "Write(.env.local)",
            "Read(.env.*.local)", "Edit(.env.*.local)", "Write(.env.*.local)",
        ]),
        _settings(deny=[
            "Read(.env)", "Edit(.env)", "Read(.env.local)", "Edit(.env.local)",
            "Read(.env.*.local)", "Edit(.env.*.local)", "Bash(rm -rf *)",
        ]),
        rows=[
            ("added", "deny: Bash(rm -rf *)", False),
            ("removed", "deny: Write(.env)", False),
            ("removed", "deny: Write(.env.*.local)", False),
            ("removed", "deny: Write(.env.local)", False),
        ],
        changes=4, decision="allow", violations=[], signals=[],
        why={
            "Write(.env)": (
                "removes a path-scoped Write rule Claude Code never consulted (file permission "
                "checks read Read(path) and Edit(path) rules only), so no effective denial is removed"
            ),
        },
    ),
    "938_single_cell_tk_pr795_shape": _one(
        _settings(deny=["Write(man/**)", "Edit(man/**)"]),
        _settings(deny=["Write(man/*.Rd)", "Edit(man/*.Rd)"]),
        rows=[
            ("added", "deny: Edit(man/*.Rd)", False),
            ("added", "deny: Write(man/*.Rd)", False),
            ("removed", "deny: Edit(man/**)", True),
            ("removed", "deny: Write(man/**)", False),
        ],
        changes=4, decision="require_review", violations=[DENY_REMOVED],
        signals=["deny_rule_removed: claude-code:Edit(man/**)"],
    ),
    "938_write_allow_added": _one(
        _settings(allow=["Read"]),
        _settings(allow=["Read", "Write(docs/**)"]),
        rows=[("added", "allow: Write(docs/**)", False)],
        changes=1, decision="allow", violations=[], signals=[],
        why={
            "Write(docs/**)": (
                "Claude Code accepts a path-scoped Write rule but never consults it (file "
                "permission checks read Read(path) and Edit(path) rules only), so it allows "
                "and restricts nothing"
            ),
        },
    ),
    "938_bare_write_deny_still_meaningful": _one(
        _settings(deny=["Write", "Read(.env)"]),
        _settings(deny=["Read(.env)"]),
        rows=[("removed", "deny: Write", True)],
        changes=1, decision="require_review", violations=[DENY_REMOVED],
        signals=["deny_rule_removed: claude-code:Write"],
    ),
    # ---- Controls that must still widen.
    "control_genuine_new_rule": _one(
        _settings(allow=["Bash(git status)"]),
        _settings(allow=["Bash(git status)", "Bash(npm *)"]),
        rows=[("added", "allow: Bash(npm *)", True)],
        changes=1, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=["allow_rule_added: claude-code:Bash(npm *)"],
    ),
    "control_deny_to_allow_move_under_broad_allow": _one(
        _settings(allow=["Bash(git *)"], deny=["Bash(git push *)"]),
        _settings(allow=["Bash(git *)", "Bash(git push *)"]),
        rows=[
            ("added", "allow: Bash(git push *)", True),
            ("removed", "deny: Bash(git push *)", True),
        ],
        # One rule moved from deny to allow: one change in the text, still ⚠.
        changes=1, joined_widenings=1, decision="require_review", violations=[DENY_REMOVED],
        signals=[
            "allow_rule_added: claude-code:Bash(git push *)",
            "deny_rule_removed: claude-code:Bash(git push *)",
        ],
    ),
    "control_broadened_pattern": _one(
        _settings(allow=["Bash(gh pr view *)"]),
        _settings(allow=["Bash(gh pr *)"]),
        rows=[("added", "allow: Bash(gh pr *)", True), ("removed", "allow: Bash(gh pr view *)", False)],
        changes=1, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=[
            "allow_rule_added: claude-code:Bash(gh pr *)",
            "permission_widened: claude-code:Bash(gh pr view *) -> Bash(gh pr *)",
        ],
    ),
    "control_lifted_restriction_keeps_covered_addition": _one(
        _settings(allow=["Bash(git *)"], deny=["Bash(git push --force *)"]),
        _settings(allow=["Bash(git *)", "Bash(git push *)"]),
        rows=[
            ("added", "allow: Bash(git push *)", True),
            ("removed", "deny: Bash(git push --force *)", True),
        ],
        changes=2, decision="require_review", violations=[DENY_REMOVED],
        signals=[
            "allow_rule_added: claude-code:Bash(git push *)",
            "deny_rule_removed: claude-code:Bash(git push --force *)",
        ],
    ),
    # ---- Uncertain overlaps stay unestablished: the addition keeps its marker.
    "uncertain_compound_command": _one(
        _settings(allow=["Bash(git *)"]),
        _settings(allow=["Bash(git *)", "Bash(git status && curl example.com)"]),
        rows=[("added", "allow: Bash(git status && curl example.com)", True)],
        changes=1, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=["allow_rule_added: claude-code:Bash(git status && curl example.com)"],
    ),
    "uncertain_find_delete": _one(
        _settings(allow=["Bash(find *)"]),
        _settings(allow=["Bash(find *)", "Bash(find . -delete)"]),
        rows=[("added", "allow: Bash(find . -delete)", True)],
        changes=1, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=["allow_rule_added: claude-code:Bash(find . -delete)"],
    ),
    "uncertain_double_star_path": _one(
        _settings(allow=["Edit(src/**)"]),
        _settings(allow=["Edit(src/**)", "Edit(src/app/main.ts)"]),
        rows=[("added", "allow: Edit(src/app/main.ts)", True)],
        changes=1, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=["allow_rule_added: claude-code:Edit(src/app/main.ts)"],
    ),
    "uncertain_star_crossing_a_directory": _one(
        _settings(allow=["Read(docs/*)"]),
        _settings(allow=["Read(docs/*)", "Read(docs/api/index.md)"]),
        rows=[("added", "allow: Read(docs/api/index.md)", True)],
        changes=1, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=["allow_rule_added: claude-code:Read(docs/api/index.md)"],
    ),
    # "An unanchored allow glob such as "*" ... doesn't auto-approve anything",
    # so it covers nothing ("Tool name wildcards").
    "uncertain_unanchored_star_allow": _one(
        _settings(allow=["*"]),
        _settings(allow=["*", "Bash(npm test)"]),
        rows=[("added", "allow: Bash(npm test)", True)],
        changes=1, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=["allow_rule_added: claude-code:Bash(npm test)"],
    ),
    # Padding is not a documented spelling: the denial is not proven kept.
    "uncertain_padded_respelling": _one(
        _settings(deny=["Bash(rm:*)"]),
        _settings(deny=["Bash( rm *)"]),
        rows=[("added", "deny: Bash( rm *)", False), ("removed", "deny: Bash(rm:*)", True)],
        changes=2, decision="require_review", violations=[DENY_REMOVED],
        signals=["deny_rule_removed: claude-code:Bash(rm:*)"],
    ),
    # A pattern of nothing but stars may be the whole tool: kept as consulted.
    "938_whole_path_write_deny_kept": _one(
        _settings(deny=["Write(**)", "Read(.env)"]),
        _settings(deny=["Read(.env)"]),
        rows=[("removed", "deny: Write(**)", True)],
        changes=1, decision="require_review", violations=[DENY_REMOVED],
        signals=["deny_rule_removed: claude-code:Write(**)"],
    ),
    "uncertain_tool_name_case": _one(
        _settings(allow=["bash(git *)"]),
        _settings(allow=["bash(git *)", "Bash(git status *)"]),
        rows=[("added", "allow: Bash(git status *)", True)],
        changes=1, decision="require_review", violations=[ALLOW_EXPANDED],
        signals=["allow_rule_added: claude-code:Bash(git status *)"],
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_every_route_reads_the_rule_model_alike(tmp_path: Path, name: str) -> None:
    case = CASES[name]
    routes = _routes(_repository(tmp_path, case.base, case.head))

    assert routes["diff"]["comparison_status"] == "comparable", routes["diff"]
    assert _marked(routes["diff"]["rows"]) == case.rows
    assert _marked(routes["verify"]["rows"]) == case.rows
    assert _shape(routes["check"]["rows"]) == sorted(
        (direction, expands) for direction, _, expands in case.rows
    )
    assert routes["diff"]["review"] == routes["verify"]["review"]
    assert routes["diff"]["review"]["summary"]["widenings"] == case.text_widenings
    assert routes["check"]["decision"] == case.decision
    assert sorted({item["check_id"] for item in routes["check"]["violations"]}) == case.violations
    assert routes["drift"]["comparison_status"] == "comparable"
    assert routes["drift"]["expansion_signals"] == case.signals

    rows = len(case.rows)
    summary = (
        f"{case.changes} change(s)"
        + (f" from {rows} rows" if case.changes != rows else "")
        + (f", {case.text_widenings} widening what the agent may do (⚠)." if case.text_widenings else ".")
    )
    assert summary in routes["text"], routes["text"]
    assert routes["text"].count("⚠ ") == case.text_widenings, routes["text"]
    for rule, why in case.why.items():
        [row] = [
            row for row in routes["diff"]["rows"]
            if rule in (row["before"], row["after"]) and row["disposition"] is not None
        ]
        assert row["why"] == why


def test_918_minimal_reproduction_widens_exactly_three(tmp_path: Path) -> None:
    """The issue's acceptance line, stated once on its own."""

    case = CASES["918_minimal"]
    routes = _routes(_repository(tmp_path, case.base, case.head))
    widening = sorted(row["after"] for row in routes["diff"]["rows"] if row["expands"])
    assert widening == sorted(_LINT)
    # Readable before/after: each respelling is one change, both sides shown.
    assert "allow: Bash(git add:*) → allow: Bash(git add *)" in routes["text"]
    assert "allow: Bash(pnpm run lint:*) → allow: Bash(pnpm run lint *)" in routes["text"]
    respelled = [
        change for change in routes["diff"]["review"]["changes"] if change["direction"] == "respelled"
    ]
    assert len(respelled) == 2 and not any(change["expands"] for change in respelled)


def test_969_scoped_glob_is_not_worded_as_every_target(tmp_path: Path) -> None:
    case = CASES["969_scoped_glob_without_bare_tool"]
    routes = _routes(_repository(tmp_path, case.base, case.head))
    assert "matches every target of this kind" not in routes["text"]
    assert "SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW" not in json.dumps(routes["check"])
    [row] = routes["diff"]["rows"]
    assert row["severity"] == "medium"


def test_974_ordering_change_names_what_the_carve_out_follows(tmp_path: Path) -> None:
    case = CASES["974_ordering_only_now_effective"]
    routes = _routes(_repository(tmp_path, case.base, case.head))
    [change] = routes["diff"]["review"]["changes"]
    assert change["change"] == (
        "deny: Read(!.env.example) follows no earlier rule → deny: Read(.env.*)"
    )
    assert "part of a denial is lifted" in change["why"]
    # `check` redacts rule arguments, and names no rule in the carve-out text.
    assert "Read(.env.*)" not in json.dumps(routes["check"]["rows"])


def test_974_inventory_records_what_each_carve_out_follows(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {SETTINGS: _settings(deny=["Read(.env.*)"])},
        {SETTINGS: _settings(deny=["Read(!a)", "Read(.env.*)", "Read(/x)", "Read(!.env.example)"])},
    )
    inventory = json.loads(_invoke(["audit", "--host", "--workspace", str(repo), "--json"]))
    grants = {
        grant["rule"]: grant for grant in inventory["grants"]
        if grant.get("kind") == "permission_rule"
    }
    assert grants["Read(!a)"]["carves_from"] == []
    assert grants["Read(!.env.example)"]["carves_from"] == ["Read(.env.*)"]
    assert "carves_from" not in grants["Read(.env.*)"]
