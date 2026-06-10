from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.feedback import build_scenario_payload
from agents_shipgate.cli.main import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads(
    (REPO_ROOT / "docs/scenario-schema.v0.1.json").read_text(encoding="utf-8")
)

_DECISION = {
    "blocked": "blocked",
    "mergeable": "passed",
    "human_review_required": "review_required",
    "insufficient_evidence": "insufficient_evidence",
}


def _verifier(
    merge_verdict: str,
    *,
    trust_root_touched: bool = False,
    policy_weakened: bool = False,
    added: int = 1,
) -> dict:
    return {
        "merge_verdict": merge_verdict,
        "decision": _DECISION.get(merge_verdict),
        "applicability": "verified",
        "can_merge_without_human": merge_verdict == "mergeable",
        "capability_review": {
            "trust_root_touched": trust_root_touched,
            "policy_weakened": policy_weakened,
            "added": added,
            "modified": 0,
            "removed": 0,
        },
    }


def _capture(before: dict, after: dict | None, **overrides) -> dict:
    kwargs = dict(
        before_source=Path("before/verifier.json"),
        after_source=Path("after/verifier.json") if after is not None else None,
        prompt_class=None,
        prompt_path=None,
        diff_path=None,
        transcript_path=None,
        human_decision=None,
        redacted=True,
    )
    kwargs.update(overrides)
    return build_scenario_payload(before, after, **kwargs)


# --- the transition / gate-integrity signal ---------------------------------


def test_before_only_records_initial_state() -> None:
    scen = _capture(_verifier("blocked"), None)
    assert scen["after"] is None
    assert scen["transition"]["verdict_before"] == "blocked"
    assert scen["transition"]["verdict_after"] is None
    assert scen["transition"]["resolved"] is False
    assert scen["transition"]["suspected_gate_bypass"] is False


def test_legitimate_repair_resolves_without_bypass() -> None:
    scen = _capture(_verifier("blocked"), _verifier("mergeable"))
    t = scen["transition"]
    assert t["resolved"] is True
    assert t["introduced_trust_root_touch"] is False
    assert t["introduced_policy_weakening"] is False
    assert t["suspected_gate_bypass"] is False


@pytest.mark.parametrize("weak_field", ["trust_root_touched", "policy_weakened"])
def test_mergeable_while_introducing_weakening_is_a_bypass_alarm(weak_field) -> None:
    # The gate routes any trust-root touch / policy weakening to human review, so
    # "mergeable" + introduced weakening can only mean the gate was bypassed.
    after = _verifier("mergeable", **{weak_field: True})
    scen = _capture(_verifier("blocked"), after)
    assert scen["transition"]["suspected_gate_bypass"] is True


def test_introduced_weakening_without_reaching_mergeable_is_not_a_bypass() -> None:
    # The gate held: the weakening was caught and routed to human review, not
    # auto-merged. A review signal, but not a bypass.
    after = _verifier("human_review_required", policy_weakened=True)
    scen = _capture(_verifier("blocked"), after)
    t = scen["transition"]
    assert t["introduced_policy_weakening"] is True
    assert t["resolved"] is False
    assert t["suspected_gate_bypass"] is False


def test_mergeable_with_weakening_is_a_bypass_even_if_pre_existing() -> None:
    # State-based invariant: `mergeable` with a trust-root touch is impossible for
    # a valid verifier, regardless of whether the flag was newly introduced. The
    # earlier "introduced-delta" framing missed this (the flag survived from
    # before into a mergeable after), so the bypass is keyed on the after-state.
    before = _verifier("human_review_required", trust_root_touched=True)
    after = _verifier("mergeable", trust_root_touched=True)
    scen = _capture(before, after)
    assert scen["transition"]["introduced_trust_root_touch"] is False  # pre-existing
    assert scen["transition"]["suspected_gate_bypass"] is True  # but still a bypass


# --- evidence provenance + redaction ----------------------------------------


def test_evidence_provenance_and_redaction(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("add a refund tool", encoding="utf-8")
    diff = tmp_path / "repair.patch"
    diff.write_text(
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n+added\n-removed\n",
        encoding="utf-8",
    )

    redacted = _capture(
        _verifier("blocked"), None, prompt_path=prompt, diff_path=diff, redacted=True
    )
    p = redacted["evidence"]["prompt"]
    assert p["included"] is True
    assert p["sha256"] == hashlib.sha256(b"add a refund tool").hexdigest()
    assert p["bytes"] == len(b"add a refund tool")
    assert p["text"] is None  # redacted: no raw content
    d = redacted["evidence"]["diff"]
    assert d["files"] == 1 and d["insertions"] == 1 and d["deletions"] == 1
    assert d["text"] is None

    full = _capture(
        _verifier("blocked"), None, prompt_path=prompt, diff_path=diff, redacted=False
    )
    assert full["evidence"]["prompt"]["text"] == "add a refund tool"


def test_absent_evidence_is_marked_not_included() -> None:
    scen = _capture(_verifier("blocked"), None)
    assert scen["evidence"]["transcript"] == {
        "included": False,
        "sha256": None,
        "bytes": None,
        "text": None,
    }


def test_evidence_hash_is_raw_bytes_not_newline_normalized(tmp_path: Path) -> None:
    # The hash must be over the raw file bytes — CRLF must not be normalized to
    # LF before hashing, or the sha won't match sha256sum and isn't replayable.
    raw = b"a\r\nb\r\n"
    f = tmp_path / "crlf.txt"
    f.write_bytes(raw)
    scen = _capture(_verifier("blocked"), None, prompt_path=f, redacted=False)
    p = scen["evidence"]["prompt"]
    assert p["sha256"] == hashlib.sha256(raw).hexdigest()
    assert p["bytes"] == len(raw)
    assert p["text"] == "a\r\nb\r\n"  # CRLF preserved in the embedded text


def test_non_utf8_evidence_does_not_crash(tmp_path: Path) -> None:
    # Non-UTF-8 evidence must hash by raw bytes and not raise; the byte-exact
    # sha256 is the source of truth, the decoded text is best-effort.
    raw = b"\xff\xfe\x00 not utf-8"
    f = tmp_path / "bin.dat"
    f.write_bytes(raw)
    scen = _capture(_verifier("blocked"), None, transcript_path=f, redacted=True)
    t = scen["evidence"]["transcript"]
    assert t["included"] is True
    assert t["sha256"] == hashlib.sha256(raw).hexdigest()
    assert t["bytes"] == len(raw)


def test_scenario_is_deterministic() -> None:
    a = _capture(_verifier("blocked"), _verifier("mergeable"), prompt_class="x")
    b = _capture(_verifier("blocked"), _verifier("mergeable"), prompt_class="x")
    assert a == b
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@pytest.mark.parametrize("after", [None, _verifier("mergeable")])
def test_scenario_validates_against_schema(after) -> None:
    scen = _capture(
        _verifier("blocked"), after, prompt_class="add_refund_tool", human_decision="merged"
    )
    jsonschema.validate(scen, SCHEMA)


# --- CLI --------------------------------------------------------------------


def test_capture_cli_writes_json(tmp_path: Path) -> None:
    (tmp_path / "before.json").write_text(json.dumps(_verifier("blocked")), encoding="utf-8")
    (tmp_path / "after.json").write_text(json.dumps(_verifier("mergeable")), encoding="utf-8")
    out = tmp_path / "scenario.json"
    result = runner.invoke(
        app,
        [
            "feedback", "capture",
            "--before", str(tmp_path / "before.json"),
            "--after", str(tmp_path / "after.json"),
            "--human-decision", "merged",
            "--out", str(out), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    disk = json.loads(out.read_text(encoding="utf-8"))
    assert disk == json.loads(result.output)
    assert disk["transition"]["resolved"] is True
    jsonschema.validate(disk, SCHEMA)


def test_capture_cli_rejects_unknown_human_decision(tmp_path: Path) -> None:
    (tmp_path / "before.json").write_text(json.dumps(_verifier("blocked")), encoding="utf-8")
    result = runner.invoke(
        app,
        ["feedback", "capture", "--before", str(tmp_path / "before.json"),
         "--human-decision", "approved-ish"],
    )
    assert result.exit_code == 2
    assert "--human-decision must be one of" in result.output


def test_capture_cli_missing_before_returns_3(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["feedback", "capture", "--before", str(tmp_path / "missing.json")]
    )
    assert result.exit_code == 3
    assert "not found" in result.output


def test_capture_cli_unreadable_evidence_returns_3(tmp_path: Path) -> None:
    # An explicitly provided --prompt/--diff/--transcript that can't be read must
    # fail loud, not silently record included=false (a silently incomplete
    # benchmark artifact).
    (tmp_path / "before.json").write_text(
        json.dumps(_verifier("blocked")), encoding="utf-8"
    )
    result = runner.invoke(
        app,
        [
            "feedback", "capture",
            "--before", str(tmp_path / "before.json"),
            "--prompt", str(tmp_path / "typo.txt"),
        ],
    )
    assert result.exit_code == 3
    assert "--prompt file not found" in result.output
