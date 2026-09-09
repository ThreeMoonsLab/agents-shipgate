"""Actual optional review evidence distinguishes pointer and receipt guarantees."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from agents_shipgate.cli.current_workspace import live_workspace
from agents_shipgate.core import current_control as control
from agents_shipgate.core.human_review_decision import _current_review
from agents_shipgate.core.verification_identity import load_validated_receipt_artifacts
from agents_shipgate.schemas.verification_identity import VerificationReceipt
from tests.test_authorization_verify_integration import _committed_review_repo, _verify


@pytest.fixture
def review_run(tmp_path):
    repo = _committed_review_repo(tmp_path)
    _, report, code = _verify(repo)
    assert code == 0 and report.release_decision.decision == "review_required"
    out = repo / "agents-shipgate-reports"
    assert (out / "human-review-request.json").is_file()
    return repo, out


def _read(repo, out, *, capture=()):
    return control.read_current_control(
        out, live=lambda: live_workspace(repo, out), capture=capture,
    )


def _closure(out):
    return load_validated_receipt_artifacts(
        receipt_path=out / "verification-receipt.json", root=out,
    )


def _damage(path: Path, mutation: str):
    if mutation == "changed":
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "symlink":
        target = path.parent / "substitute.json"
        path.rename(target)
        try:
            path.symlink_to(target.name)
        except OSError:
            pytest.skip("symlink creation is unavailable")
    else:
        assert mutation == "oversized"
        with path.open("wb") as stream:
            stream.truncate(control.MAX_BOUND_ARTIFACT_BYTES + 1)


@pytest.mark.parametrize("mutation", ["changed", "missing", "symlink", "oversized"])
def test_optional_damage_does_not_expand_compact_authority(review_run, mutation):
    repo, out = review_run
    before = _read(repo, out, capture=("verification_receipt",))
    receipt, artifacts = _closure(out)
    assert receipt == VerificationReceipt.model_validate_json(before.artifacts["verification_receipt"])
    assert "human_review_request_json" in artifacts
    assert "human_review_request_json" not in before.pointer.artifacts
    assert not before.pointer.control.permissions.merge
    assert _current_review(repo, out).receipt_id == receipt.receipt_id

    _damage(out / "human-review-request.json", mutation)

    after = _read(repo, out, capture=("human_review_request_json",))
    assert after.pointer == before.pointer
    assert after.artifacts == {}
    with pytest.raises(ValueError):
        _closure(out)
    with pytest.raises(ValueError):
        _current_review(repo, out)


@pytest.mark.parametrize("mutation", ["changed", "missing", "symlink", "oversized"])
def test_bound_damage_refuses_even_without_capture(review_run, mutation):
    repo, out = review_run
    _read(repo, out)
    _damage(out / "verifier.json", mutation)
    with pytest.raises(control.CurrentControlUnavailable) as failure:
        _read(repo, out, capture=())
    assert failure.value.reason in {"artifact_mismatch", "artifact_unreadable"}


def test_capture_changes_returned_bytes_not_validated_read_set(review_run, monkeypatch):
    repo, out = review_run
    observed = _read(repo, out)
    read_paths = []
    original = control.read_regular_file_beneath

    def record(root, logical_path, **kwargs):
        read_paths.append(logical_path)
        return original(root, logical_path, **kwargs)

    monkeypatch.setattr(control, "read_regular_file_beneath", record)
    expected_reads = Counter(ref.path for ref in observed.pointer.artifacts.values())
    # Initial pointer, post-artifact confirmation, post-live-observation confirmation.
    expected_reads["current-control.json"] = 3
    for capture in [(), ("verifier",), ("human_review_request_json",)]:
        read_paths.clear()
        result = _read(repo, out, capture=capture)
        assert Counter(read_paths) == expected_reads
        assert set(result.artifacts) == set(capture).intersection(observed.pointer.artifacts)
        assert "human-review-request.json" not in read_paths


def test_full_closure_returns_snapshot_bytes_including_optional_request(review_run):
    _, out = review_run
    path = out / "human-review-request.json"
    expected = path.read_bytes()
    receipt, artifacts = _closure(out)
    assert receipt.artifact_manifest.artifacts["human_review_request_json"].size_bytes == len(expected)
    path.write_bytes(b"changed after snapshot")
    assert artifacts["human_review_request_json"] == expected
    with pytest.raises(ValueError):
        _closure(out)
