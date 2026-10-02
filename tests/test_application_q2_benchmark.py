"""The application review benchmark stays reproducible and honest (#908).

`benchmark/application-q2/` holds the development corpus, the frozen holdout
selection rule, the runner and the per-build ledgers that #868's Q1/Q2 counts
come from. These tests hold the files to the contract the README states:
pinned commits, one hand score per member, levels that imply each other, a
summarizer whose counts are mechanical, and a holdout rule that cannot move
once frozen. They run no engine and fetch nothing.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark" / "application-q2"
SHA = re.compile(r"^[0-9a-f]{40}$")


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"application_q2_{name}", BENCH / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


summarize = _module("summarize")
DEVELOPMENT = json.loads((BENCH / "development.json").read_text(encoding="utf-8"))
MEMBERS = DEVELOPMENT["members"]
LEDGERS = sorted((BENCH / "results").glob("*.scores.json"))


def test_the_development_corpus_is_pinned_and_unique() -> None:
    assert len(MEMBERS) == 49
    slugs = [member["slug"] for member in MEMBERS]
    assert len(set(slugs)) == len(slugs)
    criteria = set(DEVELOPMENT["selection"]["criteria_ids"])
    for member in MEMBERS:
        assert SHA.match(member["merge_base"]), member["slug"]
        assert SHA.match(member["head"]), member["slug"]
        assert member["merge_base"] != member["head"], member["slug"]
        assert member["state"] in {"merged", "open"}, member["slug"]
        assert member["url"] == f"https://github.com/{member['repository']}/pull/{member['number']}"
        assert member["criteria_met"] and set(member["criteria_met"]) <= criteria, member["slug"]
    assert sum(member["state"] == "merged" for member in MEMBERS) == 46


@pytest.mark.parametrize("ledger", LEDGERS, ids=lambda path: path.name)
def test_every_member_has_one_hand_score_whose_levels_imply_each_other(ledger: Path) -> None:
    scores = json.loads(ledger.read_text(encoding="utf-8"))
    assert set(scores) == {member["slug"] for member in MEMBERS}
    for slug, score in scores.items():
        assert set(score) == {"q0", "q1", "q2", "rationale"}, slug
        assert all(isinstance(score[level], bool) for level in ("q0", "q1", "q2")), slug
        assert score["rationale"].strip(), slug
        # Q2 is Q1 and more; Q1 is only scored on a relevant pull request.
        assert not score["q2"] or score["q1"], slug
        assert not score["q1"] or score["q0"], slug


def test_the_2026_09_30_ledger_reproduces_the_published_counts() -> None:
    scores = json.loads((BENCH / "results" / "2026-09-30-6ced6f70.scores.json").read_text())
    assert sum(score["q2"] for score in scores.values()) == 1
    assert [slug for slug, score in scores.items() if score["q2"]] == ["jpka_attest-3"]
    assert sum(score["q0"] for score in scores.values()) == 41
    ledger = (BENCH / "results" / "2026-09-30-6ced6f70.md").read_text(encoding="utf-8")
    for line in ("1 `compared`, 42 `partial`, 6 `not_established`", "9 of 49", "Q2: 1/49"):
        assert line in ledger


def _write_run(out: Path, slug: str, payload: dict | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{slug}.json").write_text("" if payload is None else json.dumps(payload))


def test_the_counts_are_mechanical_and_the_levels_are_only_joined(tmp_path: Path) -> None:
    corpus = {"members": [{"slug": "a", "repository": "o/a", "number": 1, "url": "u"},
                          {"slug": "b", "repository": "o/b", "number": 2, "url": "u"},
                          {"slug": "c", "repository": "o/c", "number": 3, "url": "u"}]}
    side = {"reach": {"calls": [{"method": "GET"}], "limits": []}}
    hop = {"reach": {"calls": [], "limits": [{"at": "x.py:1"}]}}
    _write_run(tmp_path, "a", {"comparison_status": "compared", "rows": [
        {"change": "added", "before": None, "after": side},
        {"change": "changed", "before": hop, "after": hop},
    ]})
    _write_run(tmp_path, "b", {"comparison_status": "partial", "rows": []})
    _write_run(tmp_path, "c", None)  # an empty stdout is a run with no answer

    members = summarize.load_run(corpus, tmp_path)
    totals = summarize.totals(members, {"a": {"q0": True, "q1": True, "q2": False, "rationale": "x"}})

    assert totals["status"] == {"compared": 1, "no_output": 1, "partial": 1}
    assert totals["members_with_rows"] == 1
    assert totals["rows"] == {"added": 1, "removed": 0, "changed": 1, "not_established": 0}
    assert totals["sides_with_calls"] == 1
    assert totals["sides_with_unresolved_hop"] == 2
    # A level nobody scored is not counted, whatever the output says.
    assert (totals["scored"], totals["q0"], totals["q1"], totals["q2"]) == (1, 1, 1, 0)


def test_the_readme_quotes_868s_levels_verbatim() -> None:
    readme = (BENCH / "README.md").read_text(encoding="utf-8")
    for definition in (
        "**Q0 (relevant):** the PR changes an SDK/ADK agent's tools, handoffs or sub-agents in application code.",
        "**Q1 (correct and complete):** every binding change on the affected agents appears, with no false rows, and `compared` is never reported while a binding is unobservable.",
        "**Q2 (useful):** Q1, the PR modifies an agent that exists at the base, and each changed row names what the tool reaches (#872) or names the unresolved hop.",
        "**Any material omission or false row disqualifies the case.**",
    ):
        assert definition in readme


def test_the_holdout_rule_is_frozen_and_its_pins_are_generated() -> None:
    readme = (BENCH / "README.md").read_text(encoding="utf-8")
    holdout = json.loads((BENCH / "holdout.json").read_text(encoding="utf-8"))
    assert "## Holdout selection rule" in readme
    assert holdout["window_start"] == "2026-10-02T00:00:00Z"
    assert "`2026-10-02T00:00:00Z`" in readme
    assert holdout["target_members"] >= 30
    assert "first 30 members" in readme
    # Until the window holds enough pull requests the record says so, and a
    # member is only ever added with every candidate walked before it.
    if holdout["status"] == "awaiting_window":
        assert holdout["members"] == [] and holdout["skipped"] == []
    else:
        assert len(holdout["members"]) >= holdout["target_members"]
    development = {member["repository"] for member in MEMBERS}
    for member in holdout["members"]:
        assert SHA.match(member["merge_base"]) and SHA.match(member["head"])
        assert member["created_at"] >= holdout["window_start"]
        assert member["repository"] not in development


def test_the_release_runbook_records_the_count() -> None:
    runbook = (ROOT / "docs" / "release-runbook.md").read_text(encoding="utf-8")
    assert "benchmark/application-q2" in runbook
    assert "`Q2: n/49 development, m/≥30 holdout`" in runbook
