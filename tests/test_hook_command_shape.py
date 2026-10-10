"""#934: a hook row describes an inline command by its structure, or says to open the config.

CirrusRedOrg/EntityFrameworkCore.Jet#303 changed one ``PreToolUse`` command and
``diff`` 1.2.0 printed ``command changed (<not-shown> sha256:f23acba4b10f →
<not-shown> sha256:3f1dc36980b7)``: two digests, and no name because the
command opens with an assignment. Host-grants ``0.9`` (unreleased, extended in
place) now publishes on each handler's ``command`` a ``shape`` read by the
bounded, static reader of :mod:`agents_shipgate.core.hook_command_shape`: the
programs named at command positions, the counts of commands, pipes, command
substitutions, control-flow keywords and quoted strings, the redirects, and a
script path. A command the reader refuses is ``shape_limit`` instead, and its
row says the digest moved and the config has to be opened.

Pinned here: the real pair as a static fixture on every route; the grammar
(compound commands, redirects, substitutions, inline shells, the refusals);
that no argument, quoted string, URL, credential or absolute path is
published, and that every shape #987 withholds stays withheld and its
rotations quiet; what a row says for each kind of change and for a command
that is not described; that the row, severity, direction and expansion
signals are the ones the digest-only edit had; that a saved baseline holds
none of it; and that the reader is bounded and never fails on any input.
"""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.hook_command_shape import (
    MAX_COMMAND_CHARS,
    MAX_DEPTH,
    MAX_WORDS,
    UnsupportedCommand,
    parse_command,
)
from agents_shipgate.core.host_grants import (
    MAX_SHAPE_COMMANDS,
    MAX_SHAPE_REDIRECTS,
    _hook_command,
    build_host_grants_baseline,
    redacted_config_sha256,
)
from tests.test_hook_mcp_detail_fields import (
    DIGEST_REDACTED_ROTATIONS,
    GENERATED_KEY,
    GITHUB_TOKEN,
    HOOK_HEADER,
    LEAK_COMMANDS,
    SETTINGS,
    _boundary,
    _digest,
    _every_route,
    _grants,
    _inventory,
)
from tests.test_host_diff_review_changes import (
    _check,
    _diff,
    _git,
    _repository,
    _table_entry,
    _verify,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/hook_command_shape"
UNSHOWN = "hook edit; authority direction is unknown"
OPEN = "open the config to read the change"


def _stop(command: str, **handler: object) -> dict:
    return {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command, **handler}]}]}}


def _shape(command: str, **handler: object) -> dict:
    """The ``command`` a handler publishes for ``command``."""

    return _hook_command(command, handler.get("shell"))


def _row(tmp_path: Path, base: str, head: str, **handler: object) -> str:
    """The change a ``diff`` row prints for a ``Stop`` command moving from ``base`` to ``head``."""

    repo = _repository(tmp_path, {SETTINGS: _stop(base, **handler)}, {SETTINGS: _stop(head, **handler)})
    text, payload = _diff(repo)
    [row] = payload["rows"]
    # The digest-only edit's row: no direction, widening or severity is inferred.
    assert (row["direction"], row["expands"], row["severity"]) == ("changed", False, "high")
    entry = _table_entry(text, HOOK_HEADER)
    assert entry[2] == UNSHOWN
    return entry[1]


# --- the real pair, on every route -----------------------------------------


REAL_ROW = (
    "PreToolUse: command changed (<not-shown> sha256:f23acba4b10f → <not-shown> sha256:3f1dc36980b7; "
    "same programs (cat, printf, sed, grep, echo, true); simple commands 15 → 25; pipes 5 → 9; "
    "conditionals and loops 4 → 7; quoted strings 18 → 32)"
)


def _real_pair(tmp_path: Path) -> Path:
    base, head = (
        (FIXTURES / f"jet_pr303_{side}.settings.json").read_text(encoding="utf-8") for side in ("base", "head")
    )
    return _repository(tmp_path, {SETTINGS: base}, {SETTINGS: head})


def test_the_real_pair_names_what_its_two_digests_stand_for(tmp_path: Path) -> None:
    """The row the issue quoted, with the same digests, now says which programs run and how much grew."""

    repo = _real_pair(tmp_path)
    text, payload = _diff(repo)
    assert "<not-shown> sha256:f23acba4b10f → <not-shown> sha256:3f1dc36980b7" in text
    entry = _table_entry(text, HOOK_HEADER)
    assert entry[1] == REAL_ROW
    assert entry[2] == UNSHOWN
    # Nothing but the explanation moved: the row, its direction and its severity are 1.2.0's.
    [row] = payload["rows"]
    assert (row["before"], row["after"], row["direction"], row["expands"], row["severity"]) == (
        "PreToolUse", "PreToolUse", "changed", False, "high",
    )
    assert payload["review"]["summary"]["widenings"] == 0
    # The change is also described by neither quoting a pattern nor a message.
    for fragment in ("dotnet", "grep -qE", "scratchpad", "Shell edits", "LibRed"):
        assert fragment not in text + json.dumps(payload)


def test_the_real_pair_reads_the_same_on_every_route(tmp_path: Path) -> None:
    repo = _real_pair(tmp_path)
    _every_route(repo, tmp_path / "out", REAL_ROW)
    # `check`'s boundary rows are `diff`'s rows.
    assert [(r["before"], r["after"], r["direction"], r["expands"], r["why"]) for r in _boundary(repo)["rows"]] == [
        (r["before"], r["after"], r["direction"], r["expands"], r["why"]) for r in _diff(repo)[1]["rows"]
    ]


def test_the_real_pair_publishes_a_shape_and_no_text(tmp_path: Path) -> None:
    repo = _real_pair(tmp_path)
    [hook] = [g for g in _grants(repo, "hook") if g["event"] == "PreToolUse"]
    [handler] = hook["handlers"]
    command = handler["command"]
    assert command["executable"] == "<not-shown>"
    assert command["shape"] == {
        "commands": ["cat", "printf", "sed", "grep", "echo", "true"],
        "statements": 25, "pipes": 9, "substitutions": 2, "control_flow": 7, "quoted": 32,
    }
    assert "shape_limit" not in command
    published = json.dumps(_inventory(repo))
    # (`dotnet` is in this file's permission rules, so it is not a fragment to look for.)
    for fragment in ("git[[:space:]]", "scratchpad_dir", "/rewind", "checkpointing"):
        assert fragment not in published
    Draft202012Validator(
        json.loads((ROOT / "docs/host-grants-inventory-schema.v0.9.json").read_text(encoding="utf-8"))
    ).validate(_inventory(repo))


def test_a_saved_baseline_and_drift_hold_no_shape(tmp_path: Path) -> None:
    """Drift compares a baseline, which holds no handler detail, and claims no expansion."""

    repo = _real_pair(tmp_path)
    inventory = _inventory(repo)
    assert '"shape"' not in json.dumps(build_host_grants_baseline(inventory))
    _git(repo, "checkout", "-q", "main")
    result = CliRunner().invoke(app, ["audit", "--host", "--workspace", str(repo), "--save-baseline", "--json"])
    assert result.exit_code in (0, 10, 20), result.output
    _git(repo, "checkout", "-q", "change")
    result = CliRunner().invoke(app, ["audit", "--host", "--workspace", str(repo), "--drift", "--json"])
    assert result.exit_code in (0, 10, 20), result.output
    drift = json.loads(result.stdout)
    assert drift["comparison_status"] == "comparable" and drift["has_drift"] is True
    assert drift["expansion_signals"] == []
    for path in (repo / ".agents-shipgate").rglob("*.json"):
        assert '"shape"' not in path.read_text(encoding="utf-8"), path


# --- the grammar -----------------------------------------------------------


def _counts(**counts: int) -> dict:
    return {"statements": 0, "pipes": 0, "substitutions": 0, "control_flow": 0, "quoted": 0, **counts}


GRAMMAR = [
    # A simple command: its program, and nothing of its arguments.
    ("lint", {"commands": ["lint"], **_counts(statements=1)}),
    ("npm test -- --coverage", {"commands": ["npm"], **_counts(statements=1)}),
    ("/usr/local/bin/lint --fix", {"commands": ["lint"], **_counts(statements=1)}),
    ("API_KEY=x lint --fix", {"commands": ["lint"], **_counts(statements=1)}),
    # Pipelines and lists: each stage and each command is a command.
    ("cat in | jq . | tee out", {"commands": ["cat", "jq", "tee"], **_counts(statements=3, pipes=2)}),
    ("a |& b", {"commands": ["a", "b"], **_counts(statements=2, pipes=1)}),
    ("make && make test || echo failed; rm -f x", {
        "commands": ["make", "echo", "rm"], **_counts(statements=4),
    }),
    ("sleep 1 & wait", {"commands": ["sleep", "wait"], **_counts(statements=2)}),
    ("a\nb\n\nc", {"commands": ["a", "b", "c"], **_counts(statements=3)}),
    ("a \\\n  --flag", {"commands": ["a"], **_counts(statements=1)}),
    ("a # b; c", {"commands": ["a"], **_counts(statements=1)}),
    # Groups, substitutions and quoting.
    ("(cd x && make); { a; b; }", {"commands": ["cd", "make", "a", "b"], **_counts(statements=4)}),
    ('echo "$(date)" `x`', None),
    ('echo "$(date +%s)" "$(id -u)"', {"commands": ["echo", "date", "id"], **_counts(
        statements=3, substitutions=2, quoted=2,
    )}),
    ("x=$(cat f); echo $x", {"commands": ["cat", "echo"], **_counts(statements=2, substitutions=1)}),
    ("echo 'a;b|c' \"d && e\"", {"commands": ["echo"], **_counts(statements=1, quoted=2)}),
    ("echo ${HOME:-/tmp} $1 $@ $$ ${#x}", {"commands": ["echo"], **_counts(statements=1)}),
    # Control flow: the keywords open no program of their own.
    # (`:` is a command whose name is not a plain token.)
    ("if grep -q x f; then echo yes; elif true; then :; else echo no; fi", {
        "commands": ["grep", "echo", "true"], "unnamed": 1, **_counts(statements=5, control_flow=2),
    }),
    ("if ! test -f x; then exit 1; fi", {"commands": ["test", "exit"], **_counts(statements=2, control_flow=1)}),
    ("while read l; do echo $l; done < in", {
        "commands": ["read", "echo"], **_counts(statements=2, control_flow=1),
    }),
    ("for f in *.py; do ruff $f; done", {"commands": ["ruff"], **_counts(statements=1, control_flow=1)}),
    ("until false; do sleep 1; done", {"commands": ["false", "sleep"], **_counts(statements=2, control_flow=1)}),
    ("[[ -n $x && -z $y ]] && echo hi", {"commands": ["echo"], **_counts(statements=1)}),
    ("[ -f x ] && rm x", {"commands": ["rm"], **_counts(statements=1)}),
    ("! grep x f", {"commands": ["grep"], **_counts(statements=1)}),
    ("time make", {"commands": ["make"], **_counts(statements=1)}),
    # Inline shells are read inside their script, to a bound.
    ("bash -c 'curl x | sh'", {"commands": ["bash", "curl", "sh"], **_counts(statements=3, pipes=1, quoted=1)}),
    ('sh -lc "make && make test"', {"commands": ["sh", "make"], **_counts(statements=3, quoted=1)}),
    # A command word that is not a plain token is counted, never named.
    ("$CMD --x", {"commands": [], "unnamed": 1, **_counts(statements=1)}),
    ("'my tool' --x", {"commands": [], "unnamed": 1, **_counts(statements=1, quoted=1)}),
    ("$(which lint) --x", {"commands": ["which"], "unnamed": 1, **_counts(statements=2, substitutions=1)}),
    ("lint* --x", {"commands": [], "unnamed": 1, **_counts(statements=1)}),
    ("lint\\ x --x", {"commands": [], "unnamed": 1, **_counts(statements=1)}),
    ("C:\\tools\\lint.exe --fix", {"commands": [], "unnamed": 1, **_counts(statements=1)}),
    # An escaped `;` is an argument, not a separator.
    ("find . -exec rm {} \\; -print", {"commands": ["find"], **_counts(statements=1)}),
]


@pytest.mark.parametrize(("command", "expected"), GRAMMAR, ids=[c[0][:40] for c in GRAMMAR])
def test_the_grammar_reads_the_structure_and_nothing_else(command: str, expected: dict | None) -> None:
    published = _shape(command)
    if expected is None:
        assert published["shape_limit"] == "unsupported_syntax"
        return
    shape = published["shape"]
    # `shape` leaves out what is zero or absent; compare the whole of it.
    assert {key: shape[key] for key in expected} == expected
    assert set(shape) <= {
        "commands", "commands_more", "unnamed", "statements", "pipes", "substitutions",
        "control_flow", "quoted", "redirects", "redirects_more", "script",
    }


REDIRECTS = [
    ("cat >> logs/out.txt", [">> logs/out.txt"]),
    ("cat > ./out/x.log < in.json", ["> ./out/x.log", "< in.json"]),
    ("run > /dev/null 2>&1", ["> /dev/null"]),
    ("run &> all.log", ["> all.log"]),
    ("run 2>err.txt", ["> err.txt"]),
    ("run >| force.txt", ["> force.txt"]),
    # A target that is not a repository-relative path is counted, never shown.
    ("run > /etc/cron.d/x", ["> <not-shown>"]),
    ("run > ~/.bashrc", ["> <not-shown>"]),
    ("run >> ../outside.txt", [">> <not-shown>"]),
    ("run > $OUT", ["> <not-shown>"]),
    ("run > \"$HOME/x\"", ["> <not-shown>"]),
    ("run > 'a b'", ["> <not-shown>"]),
    ("run > $(mktemp)", ["> <not-shown>"]),
    ("run > https://x.invalid/path", ["> <not-shown>"]),
    (f"run > {GENERATED_KEY}", ["> <not-shown>"]),
    (f"run > {GITHUB_TOKEN}", ["> <not-shown>"]),
    # `2>&1` duplicates a descriptor and `tee y.log` is an argument: neither is a redirect to a file.
    ("run > x.log 2>&1 | tee y.log", ["> x.log"]),
]


@pytest.mark.parametrize(("command", "redirects"), REDIRECTS, ids=[c[0][:40] for c in REDIRECTS])
def test_a_redirect_publishes_its_operator_and_only_a_safe_target(command: str, redirects: list[str]) -> None:
    shape = _shape(command)["shape"]
    assert shape["redirects"] == redirects
    assert GENERATED_KEY not in json.dumps(shape) and GITHUB_TOKEN not in json.dumps(shape)


def test_redirects_past_the_bound_are_counted() -> None:
    command = "run " + " ".join(f"> f{index}.txt" for index in range(MAX_SHAPE_REDIRECTS + 3))
    shape = _shape(command)["shape"]
    assert len(shape["redirects"]) == MAX_SHAPE_REDIRECTS and shape["redirects_more"] == 3


def test_programs_past_the_bound_are_counted() -> None:
    command = "; ".join(f"tool{index}" for index in range(MAX_SHAPE_COMMANDS + 5))
    shape = _shape(command)["shape"]
    assert len(shape["commands"]) == MAX_SHAPE_COMMANDS and shape["commands_more"] == 5
    assert shape["statements"] == MAX_SHAPE_COMMANDS + 5


SCRIPTS = [
    ("python3 .claude/hooks/guard.py --mode write", ".claude/hooks/guard.py"),
    ("python -u ./scripts/guard.py", "./scripts/guard.py"),
    ("node hooks/check.mjs", "hooks/check.mjs"),
    ("bash scripts/run.sh -x", "scripts/run.sh"),
    ("bin/lint.sh --fix", "bin/lint.sh"),
    ("./hooks/lint.sh", "./hooks/lint.sh"),
    ('"$CLAUDE_PROJECT_DIR"/.claude/hooks/lint.sh', "${CLAUDE_PROJECT_DIR}/.claude/hooks/lint.sh"),
    ("${CLAUDE_PLUGIN_ROOT}/hooks/post.sh", "${CLAUDE_PLUGIN_ROOT}/hooks/post.sh"),
    ("FOO=1 python3 hooks/a.py", "hooks/a.py"),
    ("cd src && python3 tools/check.py", "tools/check.py"),
    # No script: a path argument of a program that is not an interpreter, a path
    # that leaves the repository or is absolute, a dynamic one.
    ("grep -r x scripts/run.py", None),
    ("cat scripts/run.sh | sh", None),
    ("python3 ../outside.py", None),
    ("python3 /opt/tools/run.py", None),
    ("python3 $SCRIPT.py", None),
    ("python3 -m pytest", None),
    # A script path a redaction rule rewrites is not published; one beside a
    # redacted value is, since the value is gone from the text it is read from.
    ("python3 Bearer x.py", None),
    ("python3 --token x.py", None),
    (f"python3 --token {GITHUB_TOKEN} scripts/run.py", "scripts/run.py"),
    (f"./{GENERATED_KEY}.sh", None),
]


@pytest.mark.parametrize(("command", "script"), SCRIPTS, ids=[c[0][:40] for c in SCRIPTS])
def test_a_script_is_published_by_the_rule_args_use(command: str, script: str | None) -> None:
    assert _shape(command)["shape"].get("script") == script


@pytest.mark.parametrize("shell", ["powershell", "pwsh", "/bin/zsh -l", 3])
def test_a_shell_this_reader_does_not_describe_is_a_limit(shell: object) -> None:
    assert _shape("Get-ChildItem | Select-Object Name", shell=shell)["shape_limit"] == "unsupported_shell"
    assert "shape" not in _shape("lint", shell=shell)


@pytest.mark.parametrize("shell", ["bash", "sh", None])
def test_bash_and_sh_are_described(shell: str | None) -> None:
    assert "shape" in _shape("lint", shell=shell)


# --- the refusals -----------------------------------------------------------


REFUSED = [
    "cat <<EOF\nhi\nEOF",
    "cat <<< word",
    "cat <(ls)",
    "tee >(cat)",
    "echo `date`",
    'echo "`date`"',
    "echo $((1+2))",
    "echo $'a\\nb'",
    'echo $"x"',
    "case $x in a) b;; esac",
    "f() { x; }",
    "function f { x; }",
    "coproc x",
    "select x in a b; do y; done",
    "echo 'unterminated",
    'echo "unterminated',
    "echo $(unterminated",
    "( unterminated",
    "echo )",
    "echo ${unterminated",
    "echo ${a:-$(b)}",
    "[[ unterminated",
    "a ;; b",
    "a=(1 2)",
    "((i++))",
    "run >",
    "run > ;",
    "run <>file",
    "(( x ))",
]


@pytest.mark.parametrize("command", REFUSED, ids=[repr(c)[:40] for c in REFUSED])
def test_a_form_outside_the_grammar_is_refused_whole(command: str) -> None:
    published = _shape(command)
    assert published["shape_limit"] == "unsupported_syntax"
    assert "shape" not in published
    with pytest.raises(UnsupportedCommand):
        parse_command(command)


def test_a_command_longer_than_the_bound_is_too_long() -> None:
    at_the_bound = _shape("a" * MAX_COMMAND_CHARS)["shape"]
    assert (at_the_bound["statements"], at_the_bound["unnamed"]) == (1, 1)
    assert _shape("a" * (MAX_COMMAND_CHARS + 1))["shape_limit"] == "too_long"


def test_nesting_and_word_bounds_are_refused_not_followed() -> None:
    assert "shape" in _shape("( " * MAX_DEPTH + "a" + " )" * MAX_DEPTH)
    assert _shape("( " * (MAX_DEPTH + 1) + "a" + " )" * (MAX_DEPTH + 1))["shape_limit"] == "unsupported_syntax"
    assert _shape("echo $(" * (MAX_DEPTH + 1) + ")" * (MAX_DEPTH + 1))["shape_limit"] == "unsupported_syntax"
    assert _shape("a " + " ".join("b" for _ in range(MAX_WORDS)))["shape_limit"] == "unsupported_syntax"
    # An inline shell is followed two scripts deep and no further.
    deep = "x"
    for _ in range(4):
        deep = "bash -c '" + deep.replace("'", "'\\''") + "'"
    assert "shape" in _shape(deep)


# --- what is never published ------------------------------------------------


def test_no_argument_quoted_string_url_or_absolute_path_is_published() -> None:
    command = (
        "/opt/internal-tools/bin/deploy --target prod-cluster-canary "
        "--note 'quoted-canary' https://hooks.example.invalid/path-canary?x=query-canary "
        "> /var/log/absolute-canary.log"
    )
    published = json.dumps(_shape(command))
    assert "canary" not in published and "internal-tools" not in published and "hooks.example" not in published
    assert _shape(command)["shape"]["commands"] == ["deploy"]


@pytest.mark.parametrize("command", LEAK_COMMANDS, ids=[repr(c)[:30] for c in LEAK_COMMANDS])
def test_every_command_an_earlier_cycle_leaked_from_publishes_no_secret(command: str) -> None:
    published = json.dumps(_shape(command)).lower()
    for secret in ("canary", GITHUB_TOKEN.lower(), GENERATED_KEY.lower()):
        assert secret not in published


#: Credentials the string rule can leave at a command position, and shapes #987 withholds.
WITHHELD = [
    "curl --token \\\ncontinued-canary https://x.invalid",
    "curl --token\ncontinued-canary https://x.invalid",
    "curl -u \\\nadmin:continuedpw-canary https://x.invalid",
    "curl -uuser:gluedu-canary https://x.invalid",
    "curl --user admin:userpw-canary https://x.invalid",
    "curl --user=admin:userpw-canary https://x.invalid",
    "curl --proxy-user admin:proxypw-canary https://x.invalid",
    'curl -H "Authorization: Bearer bearer-canary" https://x.invalid',
    'curl -H "Authorization: Basic basic-canary" https://x.invalid',
    'curl -H "Authorization: Digest digest-canary" https://x.invalid',
    "curl --password=pw-canary https://x.invalid",
    "curl --password pw-canary https://x.invalid",
    "tool --api-key=key-canary; next",
    f"x; {GENERATED_KEY}",
    f"x && {GITHUB_TOKEN}",
    "x | http://user:pw-canary@host-canary.corp.internal/path-canary?token=query-canary",
    "http://deploy:pw-canary@host-canary.corp.internal?token=query-canary x",
    "echo hi; https://host-canary.example/path",
]


@pytest.mark.parametrize("command", WITHHELD, ids=[repr(c)[:40] for c in WITHHELD])
def test_a_credential_shape_stays_withheld_from_the_shape(command: str) -> None:
    published = json.dumps(_shape(command)).lower()
    for secret in ("canary", GITHUB_TOKEN.lower(), GENERATED_KEY.lower()):
        assert secret not in published


@pytest.mark.parametrize("name", [n for n in DIGEST_REDACTED_ROTATIONS if n.startswith("hook")])
def test_rotating_a_withheld_credential_changes_no_shape_and_adds_no_row(tmp_path: Path, name: str) -> None:
    """A shape is read from the text the digest holds, so what the digest withholds cannot move it."""

    path, base, head = DIGEST_REDACTED_ROTATIONS[name]
    old, new = (_hook_command(side["hooks"]["Stop"][0]["hooks"][0]["command"]) for side in (base, head))
    assert old == new
    repo = _repository(tmp_path, {path: base}, {path: head})
    text, payload = _diff(repo)
    assert payload["rows"] == [] and "canary" not in text


def test_a_rotated_credential_on_a_continued_line_is_still_a_row_that_names_no_credential(tmp_path: Path) -> None:
    """The string rule leaves this value in the digest's input, so its rotation is a row; the shape names none of it."""

    base, head = (
        f"curl --token \\\n{value} https://x.invalid" for value in ("first-canary", "second-canary")
    )
    assert _shape(base)["shape"] == _shape(head)["shape"]
    row = _row(tmp_path, base, head)
    assert "canary" not in row
    assert row.endswith(f"same programs and structure; the change is in an argument or in quoted text this output does not show, {OPEN})")


def test_a_generated_looking_token_is_not_named() -> None:
    for token in (GENERATED_KEY, "Ab1" * 8, "0123456789abcdef0123456789abcdef01234567", "abcdefghij1234567890"):
        shape = _shape(f"x; {token}")["shape"]
        assert (shape["commands"], shape["unnamed"]) == (["x"], 1), token
    # Shorter than the bound, or with a separator or no digit, it is named.
    for name in ("xK9mQ2vL7pR4tW8nB", "my-hook-script-v2-final-2024", "a_long_script_name_2", "abcdefghijklmnopqrstuvwxyz"):
        assert _shape(f"x; {name}")["shape"]["commands"] == ["x", name], name
    # An ordinary long program name is still named.
    assert _shape("x; check-all-the-things-before-commit")["shape"]["commands"] == [
        "x", "check-all-the-things-before-commit",
    ]


# --- what a row says -------------------------------------------------------


CHANGES = [
    # An added pipeline stage, a removed one, an added `&&` command and an added `;` command.
    ("cat in | jq .", "cat in | jq . | sh", "programs +sh; simple commands 2 → 3; pipes 1 → 2"),
    ("cat in | jq . | sh", "cat in | jq .", "programs -sh; simple commands 3 → 2; pipes 2 → 1"),
    ("make", "make && curl -s x", "programs +curl; simple commands 1 → 2"),
    ("make", "make; rm -rf build", "programs +rm; simple commands 1 → 2"),
    # An added redirect and a moved one.
    ("make", "make >> logs/make.log", "redirects +>> logs/make.log"),
    ("make > a.log", "make > b.log", "redirects +> b.log, -> a.log"),
    ("make >> out", "make >> /etc/hosts", "redirects +>> <not-shown>, ->> out"),
    # A script path changed inside the repository, with the same program.
    (
        "python3 .claude/hooks/guard-readonly.py",
        "python3 .claude/hooks/guard-write.py",
        "script .claude/hooks/guard-readonly.py → .claude/hooks/guard-write.py",
    ),
    ("bin/a/run.sh", "bin/b/run.sh", "script bin/a/run.sh → bin/b/run.sh"),
    # One program replaced by another, which the executables already say.
    ("lint", "curl", None),
    # An added substitution, conditional and quoted string.
    ("make", "make $(git rev-parse HEAD)", "programs +git; simple commands 1 → 2; command substitutions 0 → 1"),
    ("make", "if make; then x; fi", "programs +x; simple commands 1 → 2; conditionals and loops 0 → 1"),
    ("echo a", "echo 'a' 'b'", "same programs (echo); quoted strings 0 → 2"),
]


@pytest.mark.parametrize(("base", "head", "fact"), CHANGES, ids=[c[1][:40] for c in CHANGES])
def test_a_row_names_the_shape_of_the_change(tmp_path: Path, base: str, head: str, fact: str | None) -> None:
    row = _row(tmp_path, base, head)
    if fact is None:
        assert row.startswith("Stop: command changed (") and ";" not in row
        return
    assert fact in row
    assert row.startswith("Stop: command changed (") and row.endswith(")")


def test_an_argument_edit_with_the_same_structure_says_to_open_the_config(tmp_path: Path) -> None:
    row = _row(tmp_path, "npm test -- --coverage", "npm test -- --bail")
    assert row == (
        f"Stop: command changed (npm {_digest('npm test -- --coverage')} → npm {_digest('npm test -- --bail')}; "
        f"same programs and structure; the change is in an argument or in quoted text this output does not "
        f"show, {OPEN})"
    )


@pytest.mark.parametrize(
    ("base", "head", "reasons"),
    [
        ("make", "cat <<EOF\nx\nEOF", "head command uses shell syntax this output does not describe"),
        ("echo `date`", "make", "base command uses shell syntax this output does not describe"),
        ("echo `a`", "echo `b`", (
            "base command uses shell syntax this output does not describe and head command uses "
            "shell syntax this output does not describe"
        )),
        ("make", "x " * (MAX_COMMAND_CHARS // 2 + 1), "head command is longer than 8,192 characters"),
    ],
    ids=["head", "base", "both", "too-long"],
)
def test_a_command_that_is_not_described_says_the_digest_moved_and_to_open_the_config(
    tmp_path: Path, base: str, head: str, reasons: str
) -> None:
    row = _row(tmp_path, base, head)
    assert f"; not described: {reasons}; the digest moved, {OPEN})" in row
    assert row.startswith("Stop: command changed (")


def test_a_command_not_described_says_so_on_every_route(tmp_path: Path) -> None:
    base, head = "make", "cat <<EOF\nsecret-canary\nEOF"
    repo = _repository(tmp_path, {SETTINGS: _stop(base)}, {SETTINGS: _stop(head)})
    change = (
        f"Stop: command changed (make {_digest(base)} → cat {_digest(head)}; not described: head command "
        f"uses shell syntax this output does not describe; the digest moved, {OPEN})"
    )
    _every_route(repo, tmp_path / "out", change)
    assert "canary" not in _diff(repo)[0]


def test_compound_changes_read_the_same_on_every_route(tmp_path: Path) -> None:
    base = "cat in | jq ."
    head = "cat in | jq . | sh >> logs/x.log"
    repo = _repository(tmp_path, {SETTINGS: _stop(base)}, {SETTINGS: _stop(head)})
    change = (
        f"Stop: command changed (cat {_digest(base)} → cat {_digest(head)}; programs +sh; "
        "simple commands 2 → 3; pipes 1 → 2; redirects +>> logs/x.log)"
    )
    _every_route(repo, tmp_path / "out", change)
    # `check` and `verify` carry the same row, severity and direction as `diff`.
    boundary = _boundary(repo)
    [row] = boundary["rows"]
    assert (row["direction"], row["expands"], row["severity"], row["why"]) == ("changed", False, "high", UNSHOWN)
    block, _summary, verifier = _verify(repo, tmp_path / "verify")
    assert any(change in line for line in block)
    [verified] = verifier["host_comparison"]["rows"]
    assert (verified["direction"], verified["expands"]) == ("changed", False)
    assert any(change in line for line in _check(repo))


def test_an_added_and_a_removed_hook_list_what_their_command_is_made_of(tmp_path: Path) -> None:
    command = "cat in | jq . | sh >> logs/x.log"
    repo = _repository(
        tmp_path,
        {SETTINGS: {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "cat <<EOF\nx\nEOF"}]}]}}},
        {SETTINGS: {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": command}]}]}}},
    )
    text, _payload = _diff(repo)
    assert _table_entry(text, "⚠ high added claude-code .claude/settings.json")[1] == (
        f"SessionEnd (command cat {_digest(command)} (runs cat, jq, sh; 2 pipes; 1 redirect (>> logs/x.log)))"
    )
    assert _table_entry(text, "high removed claude-code .claude/settings.json")[1] == (
        f"Stop (command cat {_digest('cat <<EOF' + chr(10) + 'x' + chr(10) + 'EOF')} "
        f"(not described: it uses shell syntax this output does not describe; open the config to read it)) → gone"
    )


def test_a_plain_command_adds_nothing_to_a_cell_or_a_replacement(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path,
        {SETTINGS: {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "npm test"}]}]}}},
        {SETTINGS: {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "npm run lint"}]}]}}},
    )
    text, _ = _diff(repo)
    assert _table_entry(text, "⚠ high added claude-code .claude/settings.json")[1] == (
        f"SessionEnd (command npm {_digest('npm run lint')})"
    )


def test_a_shell_setting_change_is_its_own_difference_and_leaves_the_command_unchanged(tmp_path: Path) -> None:
    """The same command digest under a shell this reader does not describe: the shell is named, the command is not 'changed'."""

    repo = _repository(
        tmp_path,
        {SETTINGS: _stop("make test", shell="bash")},
        {SETTINGS: _stop("make test", shell="powershell")},
    )
    text, _ = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == 'Stop: shell "bash" → "powershell"'


def test_several_handlers_name_which_command_changed(tmp_path: Path) -> None:
    def hooks(second: str) -> dict:
        return {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "make test"},
            {"type": "command", "command": second},
        ]}]}}

    repo = _repository(tmp_path, {SETTINGS: hooks("a | b")}, {SETTINGS: hooks("a | b | c")})
    text, _ = _diff(repo)
    assert _table_entry(text, HOOK_HEADER)[1] == (
        f"Stop: handler 2 command changed (a {_digest('a | b')} → a {_digest('a | b | c')}; "
        "programs +c; simple commands 2 → 3; pipes 1 → 2)"
    )


def test_a_digest_only_edit_is_told_apart_from_one_that_explains_itself(tmp_path: Path) -> None:
    """The issue's second outcome: every changed command's row either explains or says to open the config."""

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    explained = _row(tmp_path / "a", "make", "make && curl x")
    digest_only = _row(tmp_path / "b", "make -j2", "make -j4")
    assert "programs +curl" in explained and OPEN not in explained
    assert "same programs and structure" in digest_only and OPEN in digest_only


# --- the published schema ---------------------------------------------------


def test_the_schema_accepts_a_shape_and_a_limit_and_refuses_both(tmp_path: Path) -> None:
    schema = json.loads((ROOT / "docs/host-grants-inventory-schema.v0.9.json").read_text(encoding="utf-8"))
    repo = _repository(
        tmp_path,
        {SETTINGS: {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "make"}]}]}}},
        {SETTINGS: {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "a | b > c.log"},
            {"type": "command", "command": "cat <<EOF\nx\nEOF"},
            {"type": "command", "command": "a", "shell": "powershell"},
            {"type": "command", "command": "x " * (MAX_COMMAND_CHARS // 2 + 1)},
        ]}]}}},
    )
    inventory = _inventory(repo)
    validator = Draft202012Validator(schema)
    validator.validate(inventory)
    [hook] = _grants(repo, "hook")
    commands = [handler["command"] for handler in hook["handlers"]]
    assert [("shape" in c, c.get("shape_limit")) for c in commands] == [
        (True, None), (False, "unsupported_syntax"), (False, "unsupported_shell"), (False, "too_long"),
    ]
    # An unknown field of a shape is refused (the published object is closed).
    broken = json.loads(json.dumps(inventory))
    next(g for g in broken["grants"] if g["kind"] == "hook")["handlers"][0]["command"]["shape"]["raw"] = "x"
    assert list(validator.iter_errors(broken))


# --- the reader is bounded and never fails ---------------------------------


def test_the_reader_is_linear_on_adversarial_input() -> None:
    cases = [
        "a " * (MAX_COMMAND_CHARS // 2),
        "a | " * (MAX_COMMAND_CHARS // 4),
        "'" * 2 + "x" * (MAX_COMMAND_CHARS - 4),
        "${" * (MAX_COMMAND_CHARS // 2),
        "$(" * (MAX_COMMAND_CHARS // 2),
        "\\\n" * (MAX_COMMAND_CHARS // 2),
        '"' * MAX_COMMAND_CHARS,
        ">" * MAX_COMMAND_CHARS,
        "if " * (MAX_COMMAND_CHARS // 3),
        "a;" * (MAX_COMMAND_CHARS // 2),
        "bash -c '" * 300,
    ]
    for case in cases:
        started = time.perf_counter()
        _shape(case)
        assert time.perf_counter() - started < 0.5, case[:20]


def test_the_reader_either_describes_or_refuses_whatever_it_is_given() -> None:
    """Random shell-looking text: never an exception of another kind, and only plain names published."""

    rng = random.Random(934)
    alphabet = list("abcdez019 _-./=$(){}[]<>|&;'\"`\\\n\t#!*?~:,@%+") + ["if ", "then ", "fi", "do ", "done", "bash -c ", "||", "&&", "<<", ">>", "2>&1", "$(", "${", "[["]
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        try:
            shape = parse_command(text)
        except UnsupportedCommand:
            continue
        published = _hook_command(text) if text.strip() else None
        if published is None:
            continue
        assert ("shape" in published) != ("shape_limit" in published)
        for name in published.get("shape", {}).get("commands", []):
            assert re.fullmatch(r"[A-Za-z0-9._+-]{1,80}", name), (text, name)
        assert shape.pipes >= 0


def test_the_digest_and_executable_are_what_they_were(tmp_path: Path) -> None:
    for command in ("lint --fix", "FOO=1 run", "if x; then y; fi", "'a b' c", f"{GITHUB_TOKEN} run"):
        published = _shape(command)
        assert published["sha256"] == redacted_config_sha256(command)
