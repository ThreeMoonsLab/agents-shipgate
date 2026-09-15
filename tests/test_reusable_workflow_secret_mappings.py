"""#693: the named secret a job passes to a reusable workflow is compared by name.

`secrets: {credential: ${{ secrets.STAGING_TOKEN }}}` becoming
`${{ secrets.PRODUCTION_TOKEN }}` changed which declared source the callee is
offered, and no route showed it. The name is compared, never the value, and a
name says nothing about privilege, so the row never widens. A value Shipgate
cannot publish or compare — a literal, another expression, a name or a
reusable target that redacts — is a blocking limit rather than a guess, and
two values that redact alike never compare as unchanged (#767).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows
from agents_shipgate.core.host_grants import (
    _workflow_grant,
    diff_host_grants,
    host_grant_expansion_signals,
)

SOURCE = ".github/workflows/ci.yml"
ROOT = Path(__file__).resolve().parents[1]
ENV = {"NO_COLOR": "1", "GITHUB_ACTIONS": None, "FORCE_COLOR": None, "COLUMNS": "200"}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

#: The observed counterexample, as written in the issue.
STAGING = """on: pull_request
permissions:
  contents: read
jobs:
  deploy:
    uses: ./.github/workflows/deploy.yml
    secrets: {credential: "${{ secrets.STAGING_TOKEN }}"}
"""
PRODUCTION = STAGING.replace("STAGING_TOKEN", "PRODUCTION_TOKEN")


def _caller(secrets=None, *, uses="./.github/workflows/deploy.yml", job="deploy", permissions=None):
    call = {"uses": uses}
    if secrets is not None:
        call["secrets"] = secrets
    return {
        "on": "pull_request",
        "permissions": permissions if permissions is not None else {"contents": "read"},
        "jobs": {job: call},
    }


def _snapshot(value):
    return {"grants": [_workflow_grant(value, source=SOURCE)]}


def _changes(before, after):
    return diff_host_grants(_snapshot(before), _snapshot(after))


def _rows(before, after):
    changes = _changes(before, after)
    return capability_diff_rows({"changes": changes, "expansion_signals": host_grant_expansion_signals(changes)})


def _calls(value):
    return _workflow_grant(value, source=SOURCE)["reusable_calls"]


# --- the comparison ---------------------------------------------------------------


def test_the_observed_remap_is_one_named_changed_row_that_does_not_widen():
    before, after = yaml.safe_load(STAGING), yaml.safe_load(PRODUCTION)

    row, = _rows(before, after)
    assert row.subject == f"github {SOURCE}"
    assert row.direction == "changed" and row.expands is False
    assert "deploy: secret credential ← secrets.STAGING_TOKEN" in row.before
    assert "deploy: secret credential ← secrets.PRODUCTION_TOKEN" in row.after
    assert "PRODUCTION" not in row.before and "STAGING" not in row.after
    assert "different named source (deploy/credential)" in row.why
    # Names are not ranked: no privilege, availability or downstream claim.
    assert "does not establish its privilege, whether the caller has it, or what the called workflow does with it" in row.why
    assert "not reported as a widening" in row.why
    assert host_grant_expansion_signals(_changes(before, after)) == []
    old, new = (_workflow_grant(value, source=SOURCE) for value in (before, after))
    assert (old["access"], old["risk"]) == (new["access"], new["risk"])


def test_the_mapping_is_a_typed_fact_on_the_reusable_call():
    call, = _calls(yaml.safe_load(PRODUCTION))
    assert call == {
        "job": "deploy",
        "uses": "./.github/workflows/deploy.yml",
        "secrets_inherit": False,
        "secret_mappings": [
            {"destination": "credential", "source": "PRODUCTION_TOKEN", "form": "secret", "unresolved_reason": None}
        ],
    }


def test_an_unchanged_mapping_is_quiet():
    base = yaml.safe_load(STAGING)
    assert _rows(base, yaml.safe_load(STAGING)) == []


@pytest.mark.parametrize(
    "head",
    [
        # block style, unquoted
        STAGING.replace(
            '    secrets: {credential: "${{ secrets.STAGING_TOKEN }}"}',
            "    secrets:\n      credential: ${{ secrets.STAGING_TOKEN }}",
        ),
        # spacing inside the expression, single quotes
        STAGING.replace('"${{ secrets.STAGING_TOKEN }}"', "'${{secrets.STAGING_TOKEN}}'"),
        # surrounding whitespace in the value
        STAGING.replace('"${{ secrets.STAGING_TOKEN }}"', '"  ${{   secrets.STAGING_TOKEN   }} "'),
        # a comment
        STAGING + "# reviewed\n",
    ],
    ids=["block-style", "spacing-and-quotes", "surrounding-whitespace", "comment"],
)
def test_formatting_only_edits_are_quiet(head):
    assert _rows(yaml.safe_load(STAGING), yaml.safe_load(head)) == []


def test_reordering_destinations_is_quiet():
    before = _caller({"a": "${{ secrets.A }}", "b": "${{ secrets.B }}"})
    after = _caller({"b": "${{ secrets.B }}", "a": "${{ secrets.A }}"})
    assert _rows(before, after) == []


def test_added_removed_and_changed_destinations_are_told_apart():
    one = _caller({"credential": "${{ secrets.STAGING_TOKEN }}"})
    two = _caller({"credential": "${{ secrets.STAGING_TOKEN }}", "deploy_key": "${{ secrets.DEPLOY_KEY }}"})

    added, = _rows(one, two)
    assert "is now passed a named secret (deploy/deploy_key)" in added.why
    assert "different named source" not in added.why
    assert "deploy: secret deploy_key ← secrets.DEPLOY_KEY" in added.after
    # The unchanged mapping is not repeated on either side.
    assert "STAGING_TOKEN" not in added.before + added.after
    assert not added.expands

    removed, = _rows(two, one)
    assert "is no longer passed a named secret (deploy/deploy_key)" in removed.why
    assert "deploy: secret deploy_key ← secrets.DEPLOY_KEY" in removed.before
    assert not removed.expands

    # The same source under another destination is a removal and an addition,
    # not a changed source.
    renamed, = _rows(one, _caller({"token": "${{ secrets.STAGING_TOKEN }}"}))
    assert "is now passed a named secret (deploy/token)" in renamed.why
    assert "is no longer passed a named secret (deploy/credential)" in renamed.why
    assert "different named source" not in renamed.why

    mixed, = _rows(two, _caller({"credential": "${{ secrets.PRODUCTION_TOKEN }}"}))
    assert "different named source (deploy/credential)" in mixed.why
    assert "no longer passed a named secret (deploy/deploy_key)" in mixed.why


def test_the_same_destination_in_two_jobs_stays_two_mappings():
    def jobs(source):
        return {
            "on": "push",
            "permissions": {"contents": "read"},
            "jobs": {
                "staging": {"uses": "./.github/workflows/deploy.yml", "secrets": {"credential": "${{ secrets.STAGING_TOKEN }}"}},
                "production": {"uses": "./.github/workflows/deploy.yml", "secrets": {"credential": source}},
            },
        }

    row, = _rows(jobs("${{ secrets.PRODUCTION_TOKEN }}"), jobs("${{ secrets.STAGING_TOKEN }}"))
    assert "different named source (production/credential)" in row.why
    assert "staging/credential" not in row.why
    assert "production: secret credential ← secrets.STAGING_TOKEN" in row.after


def test_inherited_secrets_keep_their_own_meaning():
    inherit = _caller("inherit")
    named = _caller({"credential": "${{ secrets.STAGING_TOKEN }}"})

    call, = _calls(inherit)
    assert call["secrets_inherit"] is True and "secret_mappings" not in call

    widened, = _rows(named, inherit)
    assert widened.expands and widened.direction == "widened" and "secrets: inherit" in widened.after
    narrowed, = _rows(inherit, named)
    assert not narrowed.expands
    assert "is now passed a named secret (deploy/credential)" in narrowed.why


def test_a_new_reusable_target_with_the_same_mapping_names_only_the_target():
    before = _caller({"credential": "${{ secrets.STAGING_TOKEN }}"}, uses="org/repo/.github/workflows/deploy.yml@v1")
    after = _caller({"credential": "${{ secrets.STAGING_TOKEN }}"}, uses="org/repo/.github/workflows/deploy.yml@v2")

    row, = _rows(before, after)
    assert "deploy.yml@v2" in row.after and not row.expands
    assert "secret credential" not in row.before + row.after
    assert "named secret" not in row.why and "named source" not in row.why


def test_a_call_without_secrets_keeps_its_earlier_shape():
    for secrets in (None, {}):
        call, = _calls(_caller(secrets))
        assert call == {"job": "deploy", "uses": "./.github/workflows/deploy.yml", "secrets_inherit": False}


# --- values that are not a supported source ----------------------------------------

_LITERAL = "LITERALSECRETCANARY-5f1d"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (_LITERAL, "literal_value"),
        ("", "literal_value"),
        ("${{ github.token }}", "expression"),
        ("${{ secrets.A || secrets.B }}", "expression"),
        ("${{ secrets['STAGING_TOKEN'] }}", "expression"),
        (f"${{{{ format('{{0}}', '{_LITERAL}') }}}}", "expression"),
        ("prefix-${{ secrets.STAGING_TOKEN }}", "expression"),
        (12345, "not_a_string"),
        (None, "not_a_string"),
        ({"nested": _LITERAL}, "not_a_string"),
    ],
    ids=[
        "literal", "empty", "github-token", "fallback", "index-form", "literal-in-expression",
        "partial", "number", "null", "mapping",
    ],
)
def test_an_unsupported_value_publishes_nothing_of_itself(value, reason):
    grant = _workflow_grant(_caller({"credential": value}), source=SOURCE)

    entry, = grant["reusable_calls"][0]["secret_mappings"]
    assert entry == {"destination": "credential", "source": None, "form": "unresolved", "unresolved_reason": reason}
    published = json.dumps(grant)
    assert _LITERAL not in published
    assert "github.token" not in published and "STAGING_TOKEN" not in published


@pytest.mark.parametrize("secrets", ["INHERIT", ["credential"], 7, True])
def test_secrets_that_are_neither_inherit_nor_a_mapping_are_one_unresolved_entry(secrets):
    call, = _calls(_caller(secrets))
    assert call["secrets_inherit"] is False
    assert call["secret_mappings"] == [
        {"destination": None, "source": None, "form": "unresolved", "unresolved_reason": "secrets_not_a_mapping"}
    ]


def test_an_empty_secrets_key_is_not_read_as_none_declared():
    call, = _calls(yaml.safe_load(STAGING.replace('secrets: {credential: "${{ secrets.STAGING_TOKEN }}"}', "secrets:")))
    assert call["secret_mappings"][0]["unresolved_reason"] == "secrets_not_a_mapping"


def test_an_unsupported_mapping_is_a_blocking_github_limit(tmp_path):
    from agents_shipgate.cli.host_audit import host_audit_inventory

    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(_caller({"credential": _LITERAL})))

    inventory = host_audit_inventory(tmp_path)
    issue, = [item for item in inventory["issues"] if item["host"] == "github"]
    assert issue["kind"] == "unsupported" and issue["blocking"] is True
    assert "secret mapping is a literal value, an expression or another unsupported form" in issue["message"]
    coverage, = [item for item in inventory["host_coverage"] if item["host"] == "github"]
    assert coverage["status"] == "partial"
    assert _LITERAL not in json.dumps(inventory)


# --- redaction: distinct values that display alike --------------------------------

_AWS_A, _AWS_B = "AKIA" + "A" * 16, "AKIA" + "B" * 16
_GH_A, _GH_B = "ghp_" + "A" * 36, "ghp_" + "B" * 36
_TOKEN_A, _TOKEN_B = "aaaaaaaa", "bbbbbbbb"


def _digests(canary: str) -> list[str]:
    digest = hashlib.sha256(canary.encode()).hexdigest()
    return [digest, digest[:24], digest[:12]]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _write(repo, {".gitignore": "agents-shipgate-reports/\n", **files})
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def _write(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _yaml(value) -> str:
    return yaml.safe_dump(value, sort_keys=False)


def _invoke(*args: str):
    return CliRunner().invoke(app, list(args), env=ENV)


def _output(result) -> str:
    text = result.output
    try:
        text += result.stderr
    except (AttributeError, ValueError):
        pass
    if result.exception is not None:
        text += repr(result.exception)
    return _ANSI.sub("", text)


def _diff(repo: Path, *args: str) -> dict:
    result = _invoke("diff", "--workspace", str(repo), "--base", "main", *args, "--json")
    assert result.exit_code == 0, _output(result)
    return json.loads(result.stdout)


#: (case, base caller, head caller, canaries, reusable call field that carries the redaction)
_COLLISIONS = [
    (
        "job-level-uses-token-assignment",
        _caller(uses=f"org/repo/.github/workflows/x.yml@token={_TOKEN_A}"),
        _caller(uses=f"org/repo/.github/workflows/x.yml@token={_TOKEN_B}"),
        (_TOKEN_A, _TOKEN_B),
        "uses",
    ),
    (
        "job-level-uses-github-token",
        _caller(uses=f"org/repo/.github/workflows/x.yml@{_GH_A}"),
        _caller(uses=f"org/repo/.github/workflows/x.yml@{_GH_B}"),
        (_GH_A, _GH_B),
        "uses",
    ),
    (
        "secret-source-name",
        _caller({"credential": f"${{{{ secrets.{_AWS_A} }}}}"}),
        _caller({"credential": f"${{{{ secrets.{_AWS_B} }}}}"}),
        (_AWS_A, _AWS_B),
        "secret_mappings",
    ),
    (
        "secret-destination-name",
        _caller({_GH_A: "${{ secrets.STAGING_TOKEN }}"}),
        _caller({_GH_B: "${{ secrets.STAGING_TOKEN }}"}),
        (_GH_A, _GH_B),
        "secret_mappings",
    ),
]


@pytest.mark.parametrize(
    ("case", "base", "head", "canaries", "field"), _COLLISIONS, ids=[item[0] for item in _COLLISIONS]
)
def test_distinct_values_that_redact_alike_are_published_alike(case, base, head, canaries, field):
    old, new = _calls(base), _calls(head)
    # The display collision is real: without the rule these compare as equal.
    assert old == new
    call, = new
    if field == "uses":
        assert call["uses_redacted"] is True
    else:
        assert call["secret_mappings"][0]["unresolved_reason"] == "redacted"
    for canary in canaries:
        assert canary not in json.dumps(call)


@pytest.mark.parametrize(
    ("case", "base", "head", "canaries", "field"), _COLLISIONS, ids=[item[0] for item in _COLLISIONS]
)
def test_a_changed_workflow_whose_values_redact_alike_never_compares_as_unchanged(
    tmp_path, case, base, head, canaries, field
):
    repo = _repo(tmp_path, {SOURCE: _yaml(base)})
    _write(repo, {SOURCE: _yaml(head)})

    payload = _diff(repo)
    assert payload["comparison_status"] == "incomparable"
    assert {"base_inventory_incomplete", "head_inventory_incomplete"} <= set(payload["incomparable_reasons"])
    assert payload["rows"] == [] and payload["unchanged_limits"] == []

    audit = _invoke("audit", "--host", "--workspace", str(repo), "--json")
    inventory = json.loads(audit.stdout)
    issue, = [item for item in inventory["issues"] if item["host"] == "github"]
    assert issue["blocking"] and issue["kind"] == "unsupported"
    assert "credential-shaped text" in issue["message"]

    text = _invoke("diff", "--workspace", str(repo), "--base", "main")
    combined = "\n".join([json.dumps(payload), _output(audit), _output(text)])
    assert "Cannot compare against main" in _output(text)
    for canary in canaries:
        assert canary not in combined
        for digest in _digests(canary):
            assert digest not in combined


def test_an_unchanged_redacted_reusable_target_is_a_named_limit_beside_other_rows(tmp_path):
    other = ".github/workflows/release.yml"
    repo = _repo(
        tmp_path,
        {
            SOURCE: _yaml(_caller(uses=f"org/repo/.github/workflows/x.yml@token={_TOKEN_A}")),
            other: STAGING,
        },
    )
    _write(repo, {other: PRODUCTION})

    payload = _diff(repo)
    assert payload["comparison_status"] == "comparable"
    limit, = payload["unchanged_limits"]
    assert (limit["host"], limit["limit"], limit["source"]) == ("github", "unsupported", SOURCE)
    assert "a reusable workflow reference contains credential-shaped text" in limit["detail"]
    row, = payload["rows"]
    assert row["subject"].endswith("release.yml") and "secrets.PRODUCTION_TOKEN" in row["after"]
    assert _TOKEN_A not in json.dumps(payload)


def test_one_workflow_with_several_uncompared_values_names_each_in_one_limit():
    from agents_shipgate.core.host_grants import _uncompared_workflow_text

    grant = _workflow_grant(
        {
            "on": "push",
            "jobs": {
                "call": {
                    "uses": f"org/repo/.github/workflows/x.yml@{_GH_A}",
                    "secrets": {"credential": f"${{{{ secrets.{_AWS_A} }}}}", "literal": _LITERAL},
                },
                "build": {"steps": [{"uses": f"org/tool@{_GH_B}"}]},
            },
        },
        source=SOURCE,
    )
    message = _uncompared_workflow_text(grant)
    assert message == (
        "a step action reference, a reusable workflow reference and a reusable workflow secret name "
        "contain credential-shaped text; they are published redacted and cannot be compared; "
        "a reusable workflow secret mapping is a literal value, an expression or another unsupported "
        "form; its value is neither published nor compared"
    )


@pytest.mark.parametrize(
    "uses",
    [
        "./.github/workflows/deploy.yml",
        "org/repo/.github/workflows/deploy.yml@v1",
        "org/repo/.github/workflows/deploy.yml@11bd71901bbe5b1630ceea73d27597364c9af683",
        "octo-org/secret-scanner/.github/workflows/token-refresh.yml@main",
        "org/repo/.github/workflows/credentials.yml@release/2026-09",
    ],
)
def test_ordinary_reusable_targets_are_never_marked_redacted(uses):
    call, = _calls(_caller(uses=uses))
    assert call["uses"] == uses and "uses_redacted" not in call


@pytest.mark.parametrize(
    ("destination", "source"),
    [
        ("token", "GITHUB_TOKEN"),
        ("npm-token", "NPM_TOKEN"),
        ("api_key", "API_KEY"),
        ("password", "REGISTRY_PASSWORD"),
        ("aws-secret-access-key", "AWS_SECRET_ACCESS_KEY"),
        ("webhook", "SLACK_WEBHOOK_URL"),
        ("credential", "_PRIVATE_KEY2"),
    ],
)
def test_ordinary_secret_names_are_never_marked_redacted(destination, source):
    call, = _calls(_caller({destination: f"${{{{ secrets.{source} }}}}"}))
    assert call["secret_mappings"] == [
        {"destination": destination, "source": source, "form": "secret", "unresolved_reason": None}
    ]


# --- no value reaches any output ---------------------------------------------------


@pytest.mark.parametrize(
    ("head", "canaries"),
    [
        (_caller({"credential": _LITERAL}), [_LITERAL]),
        (_caller({"credential": f"${{{{ format('{{0}}', '{_LITERAL}') }}}}"}), [_LITERAL]),
        (_caller({"credential": f"${{{{ secrets.{_AWS_A} }}}}"}), [_AWS_A]),
        (_caller({_GH_A: "${{ secrets.STAGING_TOKEN }}"}), [_GH_A]),
        (_caller(uses=f"org/repo/.github/workflows/x.yml@token={_TOKEN_A}"), [_TOKEN_A]),
        (_caller(uses=f"org/repo/.github/workflows/x.yml@{_GH_B}"), [_GH_B]),
    ],
    ids=[
        "literal-value", "literal-inside-expression", "redacted-source-name",
        "redacted-destination-name", "job-uses-token-assignment", "job-uses-github-token",
    ],
)
def test_no_canary_or_digest_reaches_json_markdown_baselines_errors_or_pr_output(tmp_path, head, canaries):
    repo = _repo(tmp_path, {SOURCE: STAGING})
    baseline = tmp_path / "baseline.json"
    saved = _invoke("audit", "--host", "--workspace", str(repo), "--save-baseline", "--baseline-file", str(baseline))
    assert saved.exit_code == 0, _output(saved)
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: _yaml(head)})
    _git(repo, "commit", "-qam", "secrets")

    inventory_run = _invoke("audit", "--host", "--workspace", str(repo), "--json")
    inventory = json.loads(inventory_run.stdout)
    assert any(
        issue["host"] == "github" and issue["kind"] == "unsupported" and issue["blocking"]
        for issue in inventory["issues"]
    )

    refused = tmp_path / "second.json"
    texts = [_output(inventory_run), baseline.read_text()]
    for args in (
        ["audit", "--host", "--workspace", str(repo)],
        ["audit", "--host", "--workspace", str(repo), "--drift", "--baseline-file", str(baseline), "--json"],
        ["audit", "--host", "--workspace", str(repo), "--drift", "--baseline-file", str(baseline)],
        ["audit", "--host", "--workspace", str(repo), "--save-baseline", "--baseline-file", str(refused)],
        ["diff", "--workspace", str(repo), "--base", "main", "--json"],
        ["diff", "--workspace", str(repo), "--base", "main"],
    ):
        texts.append(_output(_invoke(*args)))
    verify = _invoke("verify", "--workspace", str(repo), "--base", "main", "--head", "HEAD", "--format", "text")
    assert verify.exit_code == 0, _output(verify)
    texts.append(_output(verify))
    reports = repo / "agents-shipgate-reports"
    texts.append((reports / "pr-comment.md").read_text())
    texts.append((reports / "verifier.json").read_text())

    # An incomplete inventory is never acknowledged, so no baseline holds it.
    assert not refused.exists()
    combined = "\n".join(texts)
    for canary in canaries:
        assert canary not in combined
        for digest in _digests(canary):
            assert digest not in combined


# --- the same row on every route ----------------------------------------------------


@pytest.fixture
def pr(tmp_path):
    repo = _repo(tmp_path, {SOURCE: STAGING})
    _git(repo, "checkout", "-qb", "change")
    _write(repo, {SOURCE: PRODUCTION})
    _git(repo, "commit", "-qam", "deploy with the production token")
    return repo


def _assert_the_row(row: dict) -> None:
    assert row["subject"] == f"github {SOURCE}"
    assert "deploy: secret credential ← secrets.STAGING_TOKEN" in row["before"]
    assert "deploy: secret credential ← secrets.PRODUCTION_TOKEN" in row["after"]
    assert "different named source (deploy/credential)" in row["why"]
    assert row["direction"] == "changed"
    assert row["expands"] is False


def test_diff_names_the_remap_in_json_and_text(pr):
    payload = _diff(pr)
    assert payload["comparison_status"] == "comparable" and payload["unchanged_limits"] == []
    row, = payload["rows"]
    _assert_the_row(row)

    text = _invoke("diff", "--workspace", str(pr), "--base", "main")
    assert text.exit_code == 0, _output(text)
    output = _output(text)
    assert "deploy: secret credential ← secrets.STAGING_TOKEN → " in output
    assert "secrets.PRODUCTION_TOKEN" in output
    assert "different named source (deploy/credential)" in output
    assert "⚠" not in output and "1 change(s)." in output


def test_diff_is_quiet_when_only_formatting_changed(tmp_path):
    repo = _repo(tmp_path, {SOURCE: STAGING})
    _write(
        repo,
        {SOURCE: STAGING.replace(
            '    secrets: {credential: "${{ secrets.STAGING_TOKEN }}"}',
            "    secrets:\n      credential: ${{secrets.STAGING_TOKEN}}  # same source",
        )},
    )
    payload = _diff(repo)
    assert (payload["comparison_status"], payload["rows"], payload["unchanged_limits"]) == ("comparable", [], [])


@pytest.mark.parametrize("preview", [True, False])
def test_manifest_free_verify_and_its_pr_comment_name_the_remap(pr, preview):
    args = ["verify", "--workspace", str(pr), "--base", "main", "--head", "HEAD", "--format", "text"]
    result = _invoke(*args, *(["--preview"] if preview else []))
    assert result.exit_code == 0, _output(result)
    assert "secrets.PRODUCTION_TOKEN" in _output(result)

    comment = (pr / "agents-shipgate-reports/pr-comment.md").read_text()
    assert "secrets.PRODUCTION_TOKEN" in comment and "deploy/credential" in comment

    verifier = json.loads((pr / "agents-shipgate-reports/verifier.json").read_text())
    comparison = verifier["host_comparison"]
    assert comparison["comparison_status"] == "comparable"
    row, = comparison["rows"]
    _assert_the_row(row)
    assert not verifier["control"]["permissions"]["merge"]


def test_check_names_the_same_remap(pr):
    args = ["check", "--workspace", str(pr), "--base", "main", "--head", "HEAD"]
    machine = _invoke(*args, "--format", "agent-boundary-json")
    assert machine.exit_code == 0, _output(machine)
    payload = json.loads(machine.stdout)
    assert payload["comparison_status"] == "comparable"
    row, = payload["rows"]
    _assert_the_row(row)

    text = _invoke(*args, "--format", "text")
    assert text.exit_code == 0, _output(text)
    assert "secrets.PRODUCTION_TOKEN" in _output(text)

    control = _invoke(*args, "--format", "agent-control-json")
    assert control.exit_code == 0, _output(control)
    block = json.loads(control.stdout)["capability_rows"]
    assert block["comparison_status"] == "comparable"
    envelope_row, = block["rows"]
    _assert_the_row(envelope_row)


# --- saved baselines ----------------------------------------------------------------


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_a_legacy_baseline_holding_a_reusable_call_does_not_assert_no_mappings(tmp_path, version):
    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import (
        build_host_drift_payload,
        build_host_grants_baseline,
        host_grants_sha256,
        load_host_grants_baseline,
    )

    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(STAGING)
    current = host_audit_inventory(tmp_path)
    legacy = build_host_grants_baseline(current)
    legacy["host_grants_schema_version"] = version
    for grant in legacy["inventory"]["grants"]:
        for call in grant.get("reusable_calls", []):
            call.pop("secret_mappings", None)
    legacy["inventory_sha256"] = host_grants_sha256(legacy["inventory"])
    baseline_path = tmp_path / "baseline.json"
    original = json.dumps(legacy)
    baseline_path.write_text(original)

    loaded = load_host_grants_baseline(baseline_path)
    drift = build_host_drift_payload(baseline=loaded, inventory=current, baseline_file=str(baseline_path))
    assert drift["comparison_status"] == "incomparable"
    assert drift["incomparable_reasons"] == [
        "baseline_reusable_workflow_secret_mappings_unavailable",
        "baseline_workflow_step_actions_unavailable",
    ]
    assert drift["has_drift"] is None and drift["changes"] == []
    assert baseline_path.read_text() == original


def test_a_current_baseline_compares_mappings_and_validates_against_the_schemas(tmp_path):
    from jsonschema import Draft202012Validator

    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import (
        build_host_drift_payload,
        build_host_grants_baseline,
    )

    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(STAGING)
    inventory = host_audit_inventory(tmp_path)
    baseline = build_host_grants_baseline(inventory)
    assert baseline["host_grants_schema_version"] == "0.6"
    Draft202012Validator(json.loads((ROOT / "docs/host-grants-inventory-schema.v0.6.json").read_text())).validate(inventory)
    Draft202012Validator(json.loads((ROOT / "docs/host-grants-baseline-schema.v0.6.json").read_text())).validate(baseline)

    unchanged = build_host_drift_payload(baseline=baseline, inventory=inventory, baseline_file="b.json")
    assert (unchanged["comparison_status"], unchanged["has_drift"]) == ("comparable", False)

    path.write_text(PRODUCTION)
    drift = build_host_drift_payload(baseline=baseline, inventory=host_audit_inventory(tmp_path), baseline_file="b.json")
    assert drift["comparison_status"] == "comparable" and drift["has_drift"] is True
    assert len(drift["changes"]) == 1 and drift["expansion_signals"] == []
    Draft202012Validator(json.loads((ROOT / "docs/host-grants-drift-schema.v0.6.json").read_text())).validate(drift)


def test_a_saved_baseline_listing_mappings_out_of_order_still_compares_equal(tmp_path):
    from agents_shipgate.cli.host_audit import host_audit_inventory
    from agents_shipgate.core.host_grants import (
        build_host_drift_payload,
        build_host_grants_baseline,
    )

    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    path.write_text(_yaml(_caller({"a": "${{ secrets.A }}", "b": "${{ secrets.B }}"})))
    inventory = host_audit_inventory(tmp_path)
    baseline = build_host_grants_baseline(inventory)
    workflow, = [grant for grant in baseline["inventory"]["grants"] if grant["kind"] == "workflow"]
    workflow["reusable_calls"][0]["secret_mappings"].reverse()

    drift = build_host_drift_payload(baseline=baseline, inventory=inventory, baseline_file="b.json")
    assert drift["changes"] == []
