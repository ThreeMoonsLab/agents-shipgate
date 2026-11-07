"""Who approved the attested change, from GitHub's reviews (#1118).

The workflow fetches ``GET /repos/{owner}/{repo}/pulls/{number}/reviews`` into
a file outside the repository; ``attest --reviews`` evaluates it against the
event's pull request and the commit the verify run evaluated, which must be
the pull request's head. The engine never calls the API, never reads a
reviews file the change could have written, and the approval never changes
the verdict.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.pr_approval import ReviewsError, evaluate_pull_request_reviews
from agents_shipgate.schemas.attestation import (
    ATTESTATION_SCHEMA_VERSION,
    AttestationApprovalV1,
)
from agents_shipgate.schemas.registry import REGISTRY_LATE_OPTIONAL_FIELDS
from tests.test_attest import _report_payload, _verifier_payload

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads(
    (REPO_ROOT / f"docs/attestation-schema.v{ATTESTATION_SCHEMA_VERSION}.json").read_text(
        encoding="utf-8"
    )
)
SAMPLE = REPO_ROOT / "samples" / "support_refund_agent"
REPO = "acme/support-agent"
PR = "42"
AUTHOR = "author-dev"
HEAD = "b" * 40
OLD = "c" * 40


def _review(
    review_id: int,
    login: str | None,
    state: str,
    *,
    at: str = "2026-11-01T10:00:00Z",
    commit: str = HEAD,
    user_type: str = "User",
    pr: str = PR,
) -> dict:
    return {
        "id": review_id,
        "user": None if login is None else {"login": login, "type": user_type},
        "state": state,
        "submitted_at": at,
        "commit_id": commit,
        "pull_request_url": f"https://api.github.com/repos/{REPO}/pulls/{pr}",
        "body": "",
    }


def _evaluate(reviews: list[dict]):
    return evaluate_pull_request_reviews(
        reviews, repository=REPO, pr_number=PR, pr_author=AUTHOR, head_sha=HEAD
    )


# --- the evaluator -----------------------------------------------------------


def test_an_approval_by_another_person_on_the_head_is_approved() -> None:
    result = _evaluate([_review(1, "reviewer-a", "APPROVED")])

    assert result.status == "approved"
    assert [(a.login, a.review_id, a.on_head) for a in result.approvers] == [
        ("reviewer-a", 1, True)
    ]


def test_an_approval_of_an_earlier_commit_is_stale() -> None:
    result = _evaluate([_review(1, "reviewer-a", "APPROVED", commit=OLD)])

    assert result.status == "approved_stale"
    assert result.approvers[0].on_head is False


def test_the_author_and_bots_never_approve_and_are_recorded() -> None:
    result = _evaluate(
        [
            _review(1, AUTHOR, "APPROVED"),
            _review(2, "renovate[bot]", "APPROVED"),
            _review(3, "ci-helper", "APPROVED", user_type="Bot"),
        ]
    )

    assert result.status == "not_approved"
    assert result.approvers == ()
    assert [(i.login, i.reason) for i in result.ignored_approvals] == [
        ("author-dev", "pr_author"),
        ("ci-helper", "bot"),
        ("renovate[bot]", "bot"),
    ]


def test_the_author_match_ignores_case() -> None:
    assert _evaluate([_review(1, "AUTHOR-dev", "APPROVED")]).status == "not_approved"


def test_a_later_change_request_from_the_same_reviewer_replaces_the_approval() -> None:
    result = _evaluate(
        [
            _review(1, "reviewer-a", "APPROVED", at="2026-11-01T10:00:00Z"),
            _review(2, "reviewer-a", "CHANGES_REQUESTED", at="2026-11-01T11:00:00Z"),
        ]
    )

    assert result.status == "changes_requested"
    assert result.approvers == ()
    assert result.changes_requested_by == ("reviewer-a",)


def test_a_change_request_by_anyone_outranks_an_approval() -> None:
    result = _evaluate(
        [
            _review(1, "reviewer-a", "APPROVED"),
            _review(2, "reviewer-b", "CHANGES_REQUESTED"),
        ]
    )

    assert result.status == "changes_requested"
    assert [a.login for a in result.approvers] == ["reviewer-a"]


def test_a_later_approval_clears_a_change_request() -> None:
    result = _evaluate(
        [
            _review(1, "reviewer-a", "CHANGES_REQUESTED", at="2026-11-01T10:00:00Z"),
            _review(2, "reviewer-a", "APPROVED", at="2026-11-01T11:00:00Z"),
        ]
    )

    assert result.status == "approved"


def test_a_dismissed_review_clears_the_standing_and_a_comment_does_not() -> None:
    dismissed = _evaluate(
        [
            _review(1, "reviewer-a", "APPROVED", at="2026-11-01T10:00:00Z"),
            _review(2, "reviewer-a", "DISMISSED", at="2026-11-01T11:00:00Z"),
        ]
    )
    commented = _evaluate(
        [
            _review(1, "reviewer-a", "APPROVED", at="2026-11-01T10:00:00Z"),
            _review(2, "reviewer-a", "COMMENTED", at="2026-11-01T11:00:00Z"),
        ]
    )

    assert dismissed.status == "not_approved"
    assert commented.status == "approved"


def test_a_review_by_a_deleted_account_is_not_an_approval() -> None:
    assert _evaluate([_review(1, None, "APPROVED")]).status == "not_approved"


def test_input_order_does_not_change_the_evaluation() -> None:
    reviews = [
        _review(3, "reviewer-b", "APPROVED", at="2026-11-01T12:00:00Z"),
        _review(1, "reviewer-a", "CHANGES_REQUESTED", at="2026-11-01T10:00:00Z"),
        _review(2, "reviewer-a", "APPROVED", at="2026-11-01T11:00:00Z"),
    ]

    assert _evaluate(reviews) == _evaluate(list(reversed(reviews)))


@pytest.mark.parametrize(
    "reviews",
    [
        {"reviews": []},
        [_review(1, "reviewer-a", "APPROVED", pr="43")],
        [{**_review(1, "reviewer-a", "APPROVED"), "pull_request_url": None}],
        [{**_review(1, "reviewer-a", "APPROVED"), "state": "LGTM"}],
        [{**_review(1, "reviewer-a", "APPROVED"), "id": "1"}],
        [{**_review(1, "reviewer-a", "APPROVED"), "commit_id": None}],
        ["not a review"],
    ],
)
def test_a_file_that_is_not_this_pull_requests_reviews_is_refused(reviews) -> None:
    with pytest.raises(ReviewsError):
        _evaluate(reviews)


# --- the attestation model ---------------------------------------------------


def _approval(**updates) -> dict:
    payload = {
        "status": "approved",
        "source": "github_pull_request_reviews",
        "source_sha256": "0" * 64,
        "pr_number": PR,
        "pr_author": AUTHOR,
        "head_sha": HEAD,
        "approvers": [
            {
                "login": "reviewer-a",
                "review_id": 1,
                "submitted_at": "2026-11-01T10:00:00Z",
                "commit_id": HEAD,
                "on_head": True,
            }
        ],
        "changes_requested_by": [],
        "ignored_approvals": [],
    }
    payload.update(updates)
    return payload


def test_a_consistent_approval_validates() -> None:
    AttestationApprovalV1.model_validate(_approval())
    AttestationApprovalV1.model_validate({"status": "not_supplied"})


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "approved", "approvers": []},
        {"status": "not_approved"},
        {"status": "approved", "changes_requested_by": ["reviewer-b"]},
        {"source_sha256": None},
        {
            "approvers": [
                {
                    "login": "reviewer-a",
                    "review_id": 1,
                    "submitted_at": "2026-11-01T10:00:00Z",
                    "commit_id": OLD,
                    "on_head": True,
                }
            ]
        },
        {
            "approvers": [
                {
                    "login": AUTHOR,
                    "review_id": 1,
                    "submitted_at": "2026-11-01T10:00:00Z",
                    "commit_id": HEAD,
                    "on_head": True,
                }
            ]
        },
    ],
)
def test_an_approval_its_own_rows_do_not_support_is_rejected(updates) -> None:
    with pytest.raises(ValidationError):
        AttestationApprovalV1.model_validate(_approval(**updates))


def test_not_supplied_carries_no_evidence() -> None:
    with pytest.raises(ValidationError):
        AttestationApprovalV1.model_validate({"status": "not_supplied", "pr_number": PR})


# --- attest --reviews --------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _verify(repo: Path, out: Path, *head: str) -> None:
    result = runner.invoke(
        app,
        [
            "verify", "--workspace", str(repo), "--config", str(repo / "shipgate.yaml"),
            "--ci-mode", "advisory", *head, "--json", "--out", str(out),
        ],
    )
    assert (out / "verification-receipt.json").is_file(), result.output


@pytest.fixture(scope="module")
def verified(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """A real verify run of one commit (``--head <sha>``), receipt included."""

    root = tmp_path_factory.mktemp("approval-verified")
    repo = root / "repo"
    shutil.copytree(SAMPLE, repo)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    head = _git(repo, "rev-parse", "HEAD")
    out = root / "verify-out"
    _verify(repo, out, "--head", head)
    return out, head


def _reports(verified: tuple[Path, str], directory: Path) -> Path:
    """A private copy of the verify output, as the workflow's --from names it."""

    out, _ = verified
    shutil.copytree(out, directory)
    return directory / "verifier.json"


def _github_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, pr: dict | None) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({} if pr is None else {"pull_request": pr}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_RUN_ID", "9001")
    monkeypatch.setenv("GITHUB_ACTOR", "merger")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_review")
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)


def _pull_request(head: str = HEAD) -> dict:
    return {
        "number": int(PR),
        "user": {"login": AUTHOR},
        "html_url": f"https://github.com/{REPO}/pull/{PR}",
        "base": {"sha": "a" * 40},
        "head": {"sha": head, "ref": "feature/x"},
    }


def _write_reports(directory: Path) -> Path:
    """A verifier written before receipts: no plan names a verified commit."""

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "verifier.json").write_text(json.dumps(_verifier_payload()), encoding="utf-8")
    (directory / "report.json").write_text(json.dumps(_report_payload()), encoding="utf-8")
    return directory / "verifier.json"


def _reviews_file(path: Path, reviews: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reviews), encoding="utf-8")
    return path


def _attest(*args: str):
    return runner.invoke(app, ["attest", *args, "--json"])


def test_attest_records_the_approval_and_leaves_the_verdict_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified: tuple[Path, str]
) -> None:
    _, head = verified
    _github_env(monkeypatch, tmp_path, pr=_pull_request(head))
    verifier = _reports(verified, tmp_path / "reports")
    reviews = _reviews_file(
        tmp_path / "runner-temp" / "reviews.json",
        [
            _review(7, "reviewer-a", "APPROVED", commit=head),
            _review(8, AUTHOR, "APPROVED", commit=head),
        ],
    )
    without = _attest("--from", str(verifier), "--ci-context", "github-actions")

    result = _attest(
        "--from", str(verifier), "--ci-context", "github-actions", "--reviews", str(reviews)
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    jsonschema.validate(payload, SCHEMA)
    approval = payload["approval"]
    assert approval["status"] == "approved"
    assert approval["source"] == "github_pull_request_reviews"
    assert approval["source_sha256"] == hashlib.sha256(reviews.read_bytes()).hexdigest()
    assert (approval["pr_number"], approval["pr_author"], approval["head_sha"]) == (
        PR,
        AUTHOR,
        head,
    )
    assert payload["head_sha"] == head
    assert [a["login"] for a in approval["approvers"]] == ["reviewer-a"]
    assert approval["ignored_approvals"] == [
        {"login": AUTHOR, "review_id": 8, "reason": "pr_author"}
    ]
    baseline = json.loads(without.output)
    assert payload["verdict"] == baseline["verdict"]
    assert {k: v for k, v in payload.items() if k != "approval"} == {
        k: v for k, v in baseline.items() if k != "approval"
    }


def test_reviews_of_a_head_other_than_the_verified_commit_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified: tuple[Path, str]
) -> None:
    """Verify ran at commit A; the event and every review name commit B.

    The pull request's head is B (a synthetic merge was verified, or the
    artifacts are another revision's), so an approval of B is not an approval
    of what this attestation verified.
    """

    _, verified_head = verified
    other = "e" * 40
    _github_env(monkeypatch, tmp_path, pr=_pull_request(other))
    verifier = _reports(verified, tmp_path / "reports")
    reviews = _reviews_file(
        tmp_path / "runner-temp" / "reviews.json",
        [_review(7, "reviewer-a", "APPROVED", commit=other)],
    )

    result = _attest(
        "--from", str(verifier), "--ci-context", "github-actions", "--reviews", str(reviews)
    )

    assert result.exit_code == 3
    assert f"verify evaluated commit {verified_head}" in result.output
    assert f"the pull request's head is {other}" in result.output
    assert '"approval"' not in result.output


def test_a_declared_head_other_than_the_verified_commit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified: tuple[Path, str]
) -> None:
    _, head = verified
    _github_env(monkeypatch, tmp_path, pr=_pull_request(head))
    verifier = _reports(verified, tmp_path / "reports")
    reviews = _reviews_file(tmp_path / "rt" / "reviews.json", [])

    result = _attest(
        "--from", str(verifier), "--ci-context", "github-actions",
        "--head-sha", "e" * 40, "--reviews", str(reviews),
    )

    assert result.exit_code == 3
    assert "is not the commit verify evaluated" in result.output


def test_reviews_need_the_receipt_that_names_the_verified_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _github_env(monkeypatch, tmp_path, pr=_pull_request())
    verifier = _write_reports(tmp_path / "reports")
    reviews = _reviews_file(tmp_path / "rt" / "reviews.json", [_review(1, "reviewer-a", "APPROVED")])

    result = _attest(
        "--from", str(verifier), "--ci-context", "github-actions", "--reviews", str(reviews)
    )

    assert result.exit_code == 3
    assert "needs the verification receipt" in result.output


def test_a_verify_run_over_uncommitted_changes_carries_no_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(SAMPLE, repo)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / "NOTES.md").write_text("not committed\n", encoding="utf-8")
    out = tmp_path / "verify-out"
    _verify(repo, out)
    _github_env(monkeypatch, tmp_path, pr=_pull_request(head))
    reviews = _reviews_file(
        tmp_path / "rt" / "reviews.json", [_review(1, "reviewer-a", "APPROVED", commit=head)]
    )

    result = _attest(
        "--from", str(out / "verifier.json"), "--ci-context", "github-actions",
        "--reviews", str(reviews),
    )

    assert result.exit_code == 3
    assert "uncommitted working-tree changes" in result.output


def test_an_attestation_without_reviews_says_not_supplied(tmp_path: Path) -> None:
    verifier = _write_reports(tmp_path / "reports")

    result = _attest("--from", str(verifier))

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["approval"] == {
        "status": "not_supplied",
        "source": None,
        "source_sha256": None,
        "pr_number": None,
        "pr_author": None,
        "head_sha": None,
        "approvers": [],
        "changes_requested_by": [],
        "ignored_approvals": [],
    }
    jsonschema.validate(payload, SCHEMA)


def test_a_reviews_file_inside_the_repository_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified: tuple[Path, str]
) -> None:
    _, head = verified
    _github_env(monkeypatch, tmp_path, pr=_pull_request(head))
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    verifier = _reports(verified, repo / "agents-shipgate-reports")
    reviews = _reviews_file(repo / "reviews.json", [_review(1, "reviewer-a", "APPROVED", commit=head)])

    result = _attest(
        "--from", str(verifier), "--ci-context", "github-actions", "--reviews", str(reviews)
    )

    assert result.exit_code == 3
    assert "inside the repository" in result.output


def test_a_link_inside_the_repository_is_refused_even_to_an_outside_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified: tuple[Path, str]
) -> None:
    _, head = verified
    _github_env(monkeypatch, tmp_path, pr=_pull_request(head))
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    verifier = _reports(verified, repo / "agents-shipgate-reports")
    outside = _reviews_file(
        tmp_path / "outside.json", [_review(1, "reviewer-a", "APPROVED", commit=head)]
    )
    link = repo / "reviews.json"
    link.symlink_to(outside)

    result = _attest(
        "--from", str(verifier), "--ci-context", "github-actions", "--reviews", str(link)
    )

    assert result.exit_code == 3
    assert "inside the repository" in result.output


def test_a_reviews_file_inside_github_workspace_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified: tuple[Path, str]
) -> None:
    _, head = verified
    _github_env(monkeypatch, tmp_path, pr=_pull_request(head))
    workspace = tmp_path / "workspace"
    verifier = _reports(verified, tmp_path / "reports")
    reviews = _reviews_file(workspace / "reviews.json", [])
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))

    result = _attest(
        "--from", str(verifier), "--ci-context", "github-actions", "--reviews", str(reviews)
    )

    assert result.exit_code == 3
    assert "inside the repository" in result.output


@pytest.mark.parametrize(
    ("ci_context", "pr", "contents", "expected"),
    [
        (None, "head", "[]", "--ci-context github-actions"),
        ("github-actions", None, "[]", "pull_request.number"),
        ("github-actions", "no-user", "[]", "pull_request.user.login"),
        ("github-actions", "head", "{not json", "not valid JSON"),
        ("github-actions", "head", '{"reviews": []}', "JSON array"),
    ],
)
def test_attest_refuses_reviews_it_cannot_evaluate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verified: tuple[Path, str],
    ci_context: str | None,
    pr: str | None,
    contents: str,
    expected: str,
) -> None:
    _, head = verified
    event = None
    if pr is not None:
        event = _pull_request(head)
        if pr == "no-user":
            del event["user"]
    _github_env(monkeypatch, tmp_path, pr=event)
    verifier = _reports(verified, tmp_path / "reports")
    reviews = tmp_path / "reviews.json"
    reviews.write_text(contents, encoding="utf-8")
    args = ["--from", str(verifier), "--reviews", str(reviews)]
    if ci_context:
        args += ["--ci-context", ci_context]

    result = _attest(*args)

    assert result.exit_code == 3
    assert expected in result.output


# --- the registry ------------------------------------------------------------


def _ingest(attestation: Path, ledger: Path):
    return runner.invoke(
        app,
        ["registry", "ingest", "--attestation", str(attestation), "--registry", str(ledger), "--json"],
    )


def _approved_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified: tuple[Path, str]
) -> Path:
    _, head = verified
    _github_env(monkeypatch, tmp_path, pr=_pull_request(head))
    verifier = _reports(verified, tmp_path / "reports")
    reviews = _reviews_file(
        tmp_path / "rt" / "reviews.json", [_review(1, "reviewer-a", "APPROVED", commit=head)]
    )
    attestation = tmp_path / "attestation.json"
    result = runner.invoke(
        app,
        [
            "attest", "--from", str(verifier), "--ci-context", "github-actions",
            "--reviews", str(reviews), "--out", str(attestation),
        ],
    )
    assert result.exit_code == 0, result.output
    return attestation


def test_ingest_copies_the_approval_and_summary_counts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified: tuple[Path, str]
) -> None:
    attestation = _approved_attestation(tmp_path, monkeypatch, verified)
    ledger = tmp_path / "registry.jsonl"

    assert _ingest(attestation, ledger).exit_code == 0
    row = json.loads(ledger.read_text(encoding="utf-8"))
    assert row["approval_status"] == "approved"
    assert row["approval"]["approvers"][0]["login"] == "reviewer-a"

    summary = runner.invoke(app, ["registry", "summary", "--registry", str(ledger), "--json"])
    assert json.loads(summary.output)["by_approval_status"] == {"approved": 1}
    verify = runner.invoke(app, ["registry", "verify", "--registry", str(ledger), "--json"])
    assert json.loads(verify.output)["ok"] is True


def _recomputed_row_id(row: dict) -> str:
    """The id ``registry ingest`` would give this row, computed without the
    row model (which refuses a row like this one)."""

    payload = {
        key: value
        for key, value in row.items()
        if key != "row_id" and not (key in REGISTRY_LATE_OPTIONAL_FIELDS and value is None)
    }
    return "att_" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _unsupported(approval: dict) -> dict:
    return {**approval, "approvers": []}


def _no_head(approval: dict) -> dict:
    return {**approval, "head_sha": None}


def _not_an_object(approval: dict) -> str:
    return "approved"


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (_unsupported, "does not match its rows"),
        (_no_head, "names its source"),
        (_not_an_object, "valid dictionary"),
    ],
)
def test_ingest_refuses_an_approval_the_attestation_could_not_carry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verified: tuple[Path, str],
    tamper,
    message: str,
) -> None:
    attestation = _approved_attestation(tmp_path, monkeypatch, verified)
    payload = json.loads(attestation.read_text(encoding="utf-8"))
    payload["approval"] = tamper(payload["approval"])
    attestation.write_text(json.dumps(payload), encoding="utf-8")
    ledger = tmp_path / "registry.jsonl"

    result = _ingest(attestation, ledger)

    assert result.exit_code == 3
    assert "cannot be recorded" in result.output and message in result.output
    assert not ledger.exists()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("approval", "unsupported", "does not match its rows"),
        ("approval_status", "changes_requested", "is not the approval block's status"),
        ("approval", None, "has no approval block"),
    ],
)
def test_a_ledger_row_whose_approval_does_not_hold_is_not_counted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verified: tuple[Path, str],
    field: str,
    value,
    message: str,
) -> None:
    """A row edited in the ledger is skipped by every reader and fails verify,
    even with its row id recomputed to match the edit."""

    attestation = _approved_attestation(tmp_path, monkeypatch, verified)
    ledger = tmp_path / "registry.jsonl"
    assert _ingest(attestation, ledger).exit_code == 0
    row = json.loads(ledger.read_text(encoding="utf-8"))
    row[field] = _unsupported(row["approval"]) if value == "unsupported" else value
    row["row_id"] = _recomputed_row_id(row)
    ledger.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    verify = json.loads(
        runner.invoke(app, ["registry", "verify", "--registry", str(ledger), "--json"]).output
    )
    summary = json.loads(
        runner.invoke(app, ["registry", "summary", "--registry", str(ledger), "--json"]).output
    )

    assert verify["ok"] is False
    assert verify["skipped_count"] == 1
    assert message in verify["skipped_rows"][0]["reason"]
    assert summary["count"] == 0 and summary["by_approval_status"] == {}


def _old_row_id(row: dict) -> str:
    """The row id a released (registry 0.4) build computed: the whole row."""

    payload = {key: value for key, value in row.items() if key != "row_id"}
    return "att_" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _old_content_sha256(row: dict) -> str:
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_a_ledger_written_before_approvals_keeps_its_ids_and_chain(tmp_path: Path) -> None:
    """Registry 0.4 rows have no approval keys; their ids and chain still verify."""

    verifier = _write_reports(tmp_path / "reports")
    attestation = tmp_path / "attestation.json"
    assert runner.invoke(
        app, ["attest", "--from", str(verifier), "--out", str(attestation)]
    ).exit_code == 0
    ledger = tmp_path / "fresh.jsonl"
    assert _ingest(attestation, ledger).exit_code == 0
    template = json.loads(ledger.read_text(encoding="utf-8"))

    # Rebuild two rows exactly as a released build wrote them.
    rows = []
    previous = None
    for run_id in ("run-1", "run-2"):
        row = {
            key: value
            for key, value in template.items()
            if key not in {"approval", "approval_status"}
        }
        row.update(
            registry_schema_version="0.4",
            run_id=run_id,
            source_attestation_sha256=hashlib.sha256(run_id.encode()).hexdigest(),
            previous_row_id=previous["row_id"] if previous else None,
            previous_row_sha256=_old_content_sha256(previous) if previous else None,
        )
        row["row_id"] = _old_row_id(row)
        rows.append(row)
        previous = row
    old_ledger = tmp_path / "released.jsonl"
    old_ledger.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )

    verify = runner.invoke(app, ["registry", "verify", "--registry", str(old_ledger), "--json"])

    payload = json.loads(verify.output)
    assert payload["ok"] is True, payload["issues"]
    assert payload["row_count"] == 2
