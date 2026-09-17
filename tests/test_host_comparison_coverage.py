"""#812 slice 1: every host comparison says what it established, source by source.

Before this, a reviewer given zero rows could not tell a docs-only change from
an `env` or `apiKeyHelper` edit the host entry does not read, or from a deleted
settings file that held only such fields: all three printed "No static
host-grant changes detected in the covered comparison". An incomparable result
named no source. The facts were already computed — the rows, the artifact
changes, the sources each inventory observed, the blocking issues — and were
dropped by the projection.

Each case is a real repository driven through `diff` (text and `--json`),
`verify` (`verifier.json`) and the PR comment `verify` writes. The refusal and
the rows are pinned unchanged beside the coverage. Nothing here adds discovery:
a source appears only because an inventory already observed it.
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
from agents_shipgate.core.host_grants import (
    build_host_boundary_snapshot,
    build_host_grants_baseline,
    host_grants_sha256,
    normalized_host_grants,
)
from agents_shipgate.report.host_comparison import coverage_lines, host_comparison_lines
from agents_shipgate.schemas.host_comparison import (
    MAX_COVERAGE_ITEMS,
    HostComparison,
    HostComparisonCoverage,
    HostComparisonCoverageItem,
)
from agents_shipgate.schemas.verifier import VerifierArtifact

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ".claude/settings.json"
LOCAL = ".claude/settings.local.json"
BASE_SETTINGS = {"permissions": {"allow": ["Read(**)"], "deny": ["Bash(curl:*)"]}}
WIDENED = {"permissions": {"allow": ["Read(**)", "Bash(*)"], "deny": []}}
HEADING = "What this run established:"
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


def _link(repo: Path, name: str, target: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def _commit(repo: Path, message: str) -> None:
    # `-f`: a fixture may commit a settings file a global ignore would hide.
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "-m", message)


def _repository(tmp_path: Path, files: dict[str, object] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, SETTINGS, BASE_SETTINGS)
    _write(repo, "README.md", "# demo\n")
    for name, value in (files or {}).items():
        _write(repo, name, value)
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "change")
    return repo


def _invoke(args: list[str]) -> str:
    result = CliRunner().invoke(app, args, env={"NO_COLOR": "1", "COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return result.output


def _diff(repo: Path) -> tuple[str, dict]:
    command = ["diff", "--workspace", str(repo), "--base", "main"]
    return _invoke(command), json.loads(_invoke([*command, "--json"]))


def _verify(repo: Path, out: Path, *, head: bool = True) -> tuple[dict, str]:
    """`verifier.json` and the PR comment one advisory `verify` run writes."""

    args = [
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--format", "text", "--base", "main",
    ]
    if head:
        args += ["--head", _git(repo, "rev-parse", "HEAD")]
    _invoke(args)
    verifier = json.loads((out / "verifier.json").read_text(encoding="utf-8"))
    return verifier, (out / "pr-comment.md").read_text(encoding="utf-8")


def _items(coverage: dict) -> list[tuple]:
    return [
        (item["source"], item["status"], item["side"], item["rows"], item["limit"], item["hosts"])
        for item in coverage["items"]
    ]


def _block(text: str) -> list[str]:
    """The coverage block of a `diff` text output, heading included."""

    lines = text.splitlines()
    start = lines.index(HEADING)
    end = next((i for i in range(start + 1, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start:end]


def _all_routes(repo: Path, tmp_path: Path, *, head: bool = True) -> tuple[str, dict, dict, str]:
    """diff text, diff JSON, verify's host comparison and PR comment, with coverage agreeing."""

    text, payload = _diff(repo)
    verifier, comment = _verify(repo, tmp_path / "out", head=head)
    comparison = verifier["host_comparison"]
    assert payload["capability_diff_schema_version"] == "0.3"
    assert verifier["verifier_schema_version"] == "0.20"
    for key in ("comparison_status", "incomparable_reasons", "rows", "unchanged_limits", "coverage"):
        assert comparison[key] == payload[key], key
    return text, payload, comparison, comment


# --- zero rows: covered no change versus a change no row describes -----------


def test_a_docs_only_change_names_the_source_it_compared_with_no_change(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, "README.md", "# demo\nmore\n")
    _commit(repo, "docs")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    assert payload["rows"] == []
    assert _items(payload["coverage"]) == [(SETTINGS, "compared", "both", 0, None, ["claude-code"])]
    assert payload["coverage"]["omitted_items"] == 0
    assert "No static host-grant changes detected. No verdict is implied." in text
    assert _block(text) == [
        HEADING,
        f"  compared with no change in what this entry reads: {SETTINGS}",
    ]
    assert (
        "No static host-grant changes detected in the covered comparison. No verdict is implied.\n"
        f"{HEADING}\n"
        f"- compared with no change in what this entry reads: ` {SETTINGS} `\n"
    ) in comment
    assert "fields this entry does not read" not in text + comment


@pytest.mark.parametrize(
    "head_settings",
    [
        {**BASE_SETTINGS, "env": {"ANTHROPIC_BASE_URL": "https://proxy.example"}},
        {**BASE_SETTINGS, "apiKeyHelper": "./scripts/key.sh"},
        {**BASE_SETTINGS, "outputStyle": "Explanatory"},
    ],
    ids=["env", "apiKeyHelper", "outputStyle"],
)
def test_a_change_in_unread_fields_is_not_described_as_no_change(
    tmp_path: Path, head_settings: dict
) -> None:
    """The verified silent case: zero rows, yet the inventory digests differ."""

    repo = _repository(tmp_path)
    _write(repo, SETTINGS, head_settings)
    _commit(repo, "unread fields")

    text, payload, comparison, comment = _all_routes(repo, tmp_path)

    assert payload["comparison_status"] == "comparable" and payload["rows"] == []
    assert comparison["base_inventory_sha256"] != comparison["head_inventory_sha256"]
    assert _items(payload["coverage"]) == [
        (SETTINGS, "unread_fields_changed", "both", 0, None, ["claude-code"])
    ]
    finding = "compared; observed a change in fields this entry does not read, so no row"
    assert _block(text) == [HEADING, f"  {SETTINGS} (claude-code): {finding}"]
    assert f"- ` {SETTINGS} ` (claude-code): {finding}\n" in comment
    assert "compared with no change" not in text + comment


# --- one side only: deleted, base-only and untracked sources -----------------


@pytest.mark.parametrize(
    ("base_files", "deleted", "expected", "line"),
    [
        (
            {LOCAL: {"env": {"FOO": "1"}}},
            LOCAL,
            (LOCAL, "unread_fields_changed", "base", 0),
            f"  {LOCAL} (claude-code): read in base only; observed a change in fields "
            "this entry does not read, so no row",
        ),
        (
            {},
            SETTINGS,
            (SETTINGS, "compared", "base", 2),
            f"  {SETTINGS} (claude-code): read in base only; 2 rows",
        ),
    ],
    ids=["env-only-file", "file-with-rules"],
)
def test_a_deleted_source_stays_attributable_to_the_base(
    tmp_path: Path, base_files: dict, deleted: str, expected: tuple, line: str
) -> None:
    repo = _repository(tmp_path, base_files)
    (repo / deleted).unlink()
    _commit(repo, "delete")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    items = _items(payload["coverage"])
    assert items[0][:4] == expected
    assert line in _block(text)
    assert "read in base only" in comment
    assert len(payload["rows"]) == expected[3]


def test_an_untracked_local_settings_file_is_a_head_only_source(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, LOCAL, {"permissions": {"allow": ["Bash(*)"]}})

    text, payload, _comparison, comment = _all_routes(repo, tmp_path, head=False)

    assert _items(payload["coverage"]) == [
        (LOCAL, "compared", "head", 1, None, ["claude-code"]),
        (SETTINGS, "compared", "both", 0, None, ["claude-code"]),
    ]
    assert _block(text) == [
        HEADING,
        f"  {LOCAL} (claude-code): read in head only; 1 row",
        f"  compared with no change in what this entry reads: {SETTINGS}",
    ]
    assert f"- ` {LOCAL} ` (claude-code): read in head only; 1 row" in comment


# --- incomparable: each blocking source and its kind, refusal unchanged ------

SKILL = "---\nname: demo\ndescription: d\nmetadata:\n  version: 2\n---\nbody\n"


def _directory_link(repo: Path) -> None:
    _write(repo, "src/pkg/a.txt", "x\n")
    _link(repo, "docs/proxy", "../src/pkg")


def _dangling_link(repo: Path) -> None:
    _link(repo, "NOTES.md", "does/not/exist.md")


def _linked_skill(repo: Path) -> None:
    _write(repo, ".agents/skills/demo/SKILL.md", SKILL)
    _link(repo, ".claude/skills", "../.agents/skills")


def _linked_skill_with_nested_link(repo: Path) -> None:
    _linked_skill(repo)
    _link(repo, ".agents/skills/alias", "demo")


BOTH = ["base_inventory_incomplete", "head_inventory_incomplete"]
INCOMPARABLE = {
    "directory-link": (
        _directory_link, WIDENED, BOTH,
        {("docs/proxy", "unreadable", "both")},
    ),
    "dangling-link": (
        _dangling_link, WIDENED, BOTH,
        {("NOTES.md", "unreadable", "both")},
    ),
    "skill-metadata-through-link": (
        _linked_skill, {"permissions": {"allow": ["Read(**)"], "deny": []}}, BOTH,
        {
            (".agents/skills/demo/SKILL.md", "unsupported", "both"),
            (".claude/skills/demo/SKILL.md", "unsupported", "both"),
        },
    ),
    "skill-metadata-and-nested-link": (
        _linked_skill_with_nested_link, {"permissions": {"allow": ["Read(**)"], "deny": []}}, BOTH,
        {
            (".agents/skills/alias/SKILL.md", "unsupported", "both"),
            (".agents/skills/demo/SKILL.md", "unsupported", "both"),
            (".claude/skills", "unreadable", "both"),
        },
    ),
}


@_LINKS
@pytest.mark.parametrize("case", list(INCOMPARABLE))
def test_an_incomparable_result_names_each_blocking_source_and_kind(tmp_path: Path, case: str) -> None:
    prepare, head_settings, reasons, expected = INCOMPARABLE[case]
    repo = _repository(tmp_path)
    prepare(repo)
    _commit(repo, "base with a limit")
    _git(repo, "branch", "-f", "main", "HEAD")
    _write(repo, SETTINGS, head_settings)
    _commit(repo, "change")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    # The refusal itself is unchanged.
    assert payload["comparison_status"] == "incomparable"
    assert payload["incomparable_reasons"] == reasons
    assert payload["rows"] == [] and payload["unchanged_limits"] == []
    assert text.splitlines()[:2] == [
        "Cannot compare against main: " + "; ".join(reasons),
        "This is an input limit, not a finding about the change. Nothing below is a claim that the change is safe.",
    ]
    items = payload["coverage"]["items"]
    assert {(item["source"], item["limit"], item["side"]) for item in items} == expected
    assert all(item["status"] == "blocking_limit" and item["detail"] for item in items)
    for source, limit, _side in expected:
        hosts = next(item["hosts"] for item in items if item["source"] == source)
        line = f"{source} ({', '.join(hosts)}): {limit} in base and head, so neither inventory is complete"
        assert f"  {line}" in _block(text)
        assert f"- ` {source} ` ({', '.join(hosts)}): {limit} in base and head" in comment


def test_a_base_side_parse_failure_is_named_on_the_base(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {".mcp.json": "{not json\n"})
    _write(repo, ".mcp.json", {"mcpServers": {"fs": {"command": "npx", "args": ["fs"]}}})
    _commit(repo, "repair")

    text, payload, comparison, comment = _all_routes(repo, tmp_path)

    assert payload["incomparable_reasons"] == ["base_inventory_incomplete"]
    assert _items(payload["coverage"]) == [
        (".mcp.json", "blocking_limit", "base", 0, "parse_failed", ["claude-code"])
    ]
    line = ".mcp.json (claude-code): parse_failed in base, so the base inventory is incomplete"
    assert _block(text) == [HEADING, f"  {line}"]
    assert "Host capability comparison unavailable: ` base_inventory_incomplete `\n" in comment
    assert "- ` .mcp.json ` (claude-code): parse_failed in base" in comment


def test_the_incomparable_next_action_is_not_rerouted_in_this_slice(tmp_path: Path) -> None:
    """Coverage is evidence beside the control; the control route stays as it was."""

    repo = _repository(tmp_path, {".mcp.json": "{not json\n"})
    _write(repo, ".mcp.json", {"mcpServers": {}})
    _commit(repo, "repair")

    verifier, comment = _verify(repo, tmp_path / "out")

    assert verifier["host_comparison"]["coverage"]["items"]
    assert "audit --host" in str(verifier["control"]["next_action"].get("command"))
    assert "- Next command: `agents-shipgate audit --host" in comment


# --- limits already named, the cap, and the empty answer ----------------------


def test_an_unchanged_limit_is_named_once_and_not_repeated_as_coverage(tmp_path: Path) -> None:
    skill = ".claude/skills/helper/SKILL.md"
    repo = _repository(
        tmp_path, {skill: "---\nname: helper\ndescription: A helper.\neffort: extreme\n---\n\nBody.\n"}
    )
    _write(repo, SETTINGS, WIDENED)
    _commit(repo, "widen")

    text, payload, _comparison, _comment = _all_routes(repo, tmp_path)

    assert [limit["source"] for limit in payload["unchanged_limits"]] == [skill]
    assert skill not in {item["source"] for item in payload["coverage"]["items"]}
    assert _items(payload["coverage"]) == [(SETTINGS, "compared", "both", 2, None, ["claude-code"])]
    assert f"  {SETTINGS} (claude-code): compared; 2 rows" in _block(text)


def test_the_list_is_capped_with_the_changed_source_first(tmp_path: Path) -> None:
    workflow = (
        "on: [push]\npermissions:\n  contents: read\njobs:\n  t:\n"
        "    runs-on: ubuntu-latest\n    steps:\n      - run: echo {i}\n"
    )
    repo = _repository(
        tmp_path, {f".github/workflows/w{i:02d}.yml": workflow.format(i=i) for i in range(12)}
    )
    _write(repo, ".github/workflows/w05.yml", workflow.format(i=5).replace("contents: read", "contents: write"))
    _commit(repo, "widen one workflow")

    text, payload, _comparison, comment = _all_routes(repo, tmp_path)

    coverage = payload["coverage"]
    assert len(coverage["items"]) == MAX_COVERAGE_ITEMS
    assert coverage["omitted_items"] == 13 - MAX_COVERAGE_ITEMS
    assert _items(coverage)[0] == (".github/workflows/w05.yml", "compared", "both", 1, None, ["github"])
    assert _block(text) == [
        HEADING,
        "  .github/workflows/w05.yml (github): compared; 1 row",
        f"  compared with no change in what this entry reads: {SETTINGS}, "
        ".github/workflows/w00.yml, .github/workflows/w01.yml and 9 more",
    ]
    assert "and 9 more" in comment
    schema = json.loads((ROOT / "docs/verifier-schema.v0.20.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(json.loads((tmp_path / "out/verifier.json").read_text("utf-8")))


def test_a_comparison_that_read_no_source_says_so(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "README.md", "# demo\n")
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "change")
    _write(repo, "README.md", "# demo\nmore\n")
    _commit(repo, "docs")

    text, payload = _diff(repo)

    assert payload["coverage"] == {"items": [], "omitted_items": 0}
    assert text.rstrip().splitlines()[-1] == f"{HEADING} no host configuration source was compared."


# --- redaction, digests, other routes and readers ------------------------------


def test_token_shaped_paths_are_redacted_in_every_coverage_projection(tmp_path: Path) -> None:
    token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    source = f"plugins/{token}/.mcp.json"
    repo = _repository(tmp_path, {source: {"mcpServers": {"fs": {"command": "npx"}}}})
    _write(repo, source, {"mcpServers": {"fs": {"command": "npx"}}, "note": 1})
    _commit(repo, "unread plugin field")

    text, payload, comparison, comment = _all_routes(repo, tmp_path)

    published = [item["source"] for item in payload["coverage"]["items"]]
    redacted = next(path for path in published if path.startswith("plugins/"))
    assert "[REDACTED:" in redacted and redacted.endswith("/.mcp.json")
    assert token not in text
    assert token not in json.dumps(payload)
    assert token not in json.dumps(comparison)
    assert token not in comment
    assert f"{redacted} (claude-code): compared; observed a change in fields" in text


def test_coverage_stays_out_of_inventory_digests_and_baselines(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, SETTINGS, {**BASE_SETTINGS, "env": {"A": "1"}})
    _commit(repo, "env")

    _text, payload = _diff(repo)
    verifier, _comment = _verify(repo, tmp_path / "out")
    inventory = build_host_boundary_snapshot(repo).inventory

    assert payload["coverage"]["items"]
    assert verifier["host_comparison"]["head_inventory_sha256"] == host_grants_sha256(
        normalized_host_grants(inventory)
    )
    baseline = json.dumps(build_host_grants_baseline(inventory))
    for key in ('"coverage"', '"omitted_items"', '"unread_fields_changed"'):
        assert key not in baseline
        assert key not in json.dumps(inventory)


def test_check_publishes_no_coverage(tmp_path: Path) -> None:
    """`check`'s boundary result cannot carry the block, and its text does not print it."""

    repo = _repository(tmp_path)
    _write(repo, SETTINGS, {**BASE_SETTINGS, "env": {"A": "1"}})
    _commit(repo, "env")
    selection = ["--workspace", str(repo), "--base", "main", "--head", "HEAD"]

    payload = json.loads(_invoke(["check", *selection, "--format", "agent-boundary-json"]))
    text = _invoke(["check", *selection, "--format", "text"])

    assert "coverage" not in payload
    assert HEADING not in text


def test_a_v0_19_verifier_reads_with_coverage_not_recorded(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, SETTINGS, {**BASE_SETTINGS, "env": {"A": "1"}})
    _commit(repo, "env")
    verifier, _comment = _verify(repo, tmp_path / "out")
    assert verifier["host_comparison"]["coverage"]["items"]
    current = json.loads((ROOT / "docs/verifier-schema.v0.20.json").read_text(encoding="utf-8"))
    Draft202012Validator(current).validate(verifier)

    # A strict 0.19 reader rejects the new member: the reason the version moved.
    frozen = json.loads((ROOT / "docs/verifier-schema.v0.19.json").read_text(encoding="utf-8"))
    legacy = json.loads(json.dumps(verifier))
    legacy["verifier_schema_version"] = "0.19"
    assert list(Draft202012Validator(frozen).iter_errors(legacy))

    # A 0.19 artifact never recorded coverage and cannot claim it.
    with pytest.raises(ValueError, match="host comparison coverage"):
        VerifierArtifact.model_validate(legacy)
    legacy["host_comparison"].pop("coverage")
    assert not list(Draft202012Validator(frozen).iter_errors(legacy))
    read = VerifierArtifact.model_validate(legacy)
    assert read.verifier_schema_version == "0.20"
    assert read.host_comparison is not None and read.host_comparison.coverage is None
    # Not recorded prints no block, never an invented one.
    assert coverage_lines(read.host_comparison) == []
    assert HEADING not in "\n".join(host_comparison_lines(read.host_comparison, markdown=True))


def test_the_current_reader_round_trips_coverage(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo, SETTINGS, {**BASE_SETTINGS, "env": {"A": "1"}})
    _commit(repo, "env")
    verifier, _comment = _verify(repo, tmp_path / "out")

    read = VerifierArtifact.model_validate(verifier)

    assert read.host_comparison is not None
    assert read.host_comparison.model_dump(mode="json")["coverage"] == verifier["host_comparison"]["coverage"]


# --- the published shape cannot say what the comparison did not establish ----


def _item(**overrides) -> dict:
    return {"source": SETTINGS, "hosts": ["claude-code"], "side": "both", "status": "compared", **overrides}


@pytest.mark.parametrize(
    ("status", "items", "rows"),
    [
        ("comparable", [_item(status="blocking_limit", limit="unreadable")], 0),
        ("incomparable", [_item()], 0),
        ("comparable", [_item(rows=2)], 1),
        ("comparable", [_item()], 1),
    ],
    ids=["limit-on-comparable", "compared-on-incomparable", "more-rows-than-published", "row-left-unattributed"],
)
def test_the_comparison_refuses_coverage_it_did_not_establish(status: str, items: list, rows: int) -> None:
    row = {"subject": f"claude-code {SETTINGS}", "before": "—", "after": "Bash(*)",
           "direction": "added", "why": "w", "severity": "high"}
    with pytest.raises(ValueError):
        HostComparison.model_validate({
            "comparison_status": status,
            "incomparable_reasons": ["head_inventory_incomplete"] if status == "incomparable" else [],
            "head_kind": "worktree",
            "rows": [row] * rows,
            "coverage": {"items": items, "omitted_items": 0},
        })


@pytest.mark.parametrize(
    "item",
    [
        _item(status="blocking_limit"),
        _item(status="blocking_limit", limit="unreadable", rows=1),
        _item(limit="unreadable"),
        _item(detail="why"),
        _item(status="unread_fields_changed", rows=1),
        _item(hosts=[]),
        _item(side="neither"),
    ],
)
def test_an_item_keeps_its_shape(item: dict) -> None:
    with pytest.raises(ValueError):
        HostComparisonCoverageItem.model_validate(item)


def test_the_list_cannot_exceed_its_cap() -> None:
    with pytest.raises(ValueError):
        HostComparisonCoverage.model_validate(
            {"items": [_item(source=f"s{i}") for i in range(MAX_COVERAGE_ITEMS + 1)]}
        )
