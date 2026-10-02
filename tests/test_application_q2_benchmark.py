"""The application review benchmark stays reproducible and honest (#908).

`benchmark/application-q2/` holds the development corpus, the frozen holdout
rule and its recorded pool, the runner and the per-build ledgers that #868's
Q1/Q2 counts come from. These tests hold the files to the contract the README
states: pinned commits, one hand score per member that names the answer it
judged, a ledger that says what its scores say, levels that imply each other,
a summarizer whose counts are mechanical, and a holdout rule that cannot move
once frozen. They run no engine and fetch nothing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark" / "application-q2"
SHA = re.compile(r"^[0-9a-f]{40}$")
LEVELS = ("q0", "q1", "q2")


def _module(name: str):
    spec = importlib.util.spec_from_file_location(f"application_q2_{name}", BENCH / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


summarize = _module("summarize")
pool_script = _module("pool")
run = _module("run")
DEVELOPMENT = json.loads((BENCH / "development.json").read_text(encoding="utf-8"))
HOLDOUT = json.loads((BENCH / "holdout.json").read_text(encoding="utf-8"))
POOL = json.loads((BENCH / "pool.json").read_text(encoding="utf-8"))
CORPORA = {DEVELOPMENT["corpus"]: DEVELOPMENT["members"], HOLDOUT["corpus"]: HOLDOUT["members"]}
SCORES = sorted((BENCH / "results").glob("*.scores.json"))
README = (BENCH / "README.md").read_text(encoding="utf-8")


def test_the_development_corpus_is_pinned_and_unique() -> None:
    members = DEVELOPMENT["members"]
    assert len(members) == 49
    slugs = [member["slug"] for member in members]
    assert len(set(slugs)) == len(slugs)
    for member in members:
        assert SHA.match(member["merge_base"]), member["slug"]
        assert SHA.match(member["head"]), member["slug"]
        assert member["merge_base"] != member["head"], member["slug"]
        assert member["state"] in {"merged", "open"}, member["slug"]
        assert member["url"] == f"https://github.com/{member['repository']}/pull/{member['number']}"
        assert member["admitted_by"] in DEVELOPMENT["selection"], member["slug"]
        assert set(member["frameworks"]) <= {"openai_agents_sdk", "google_adk"}, member["slug"]
    assert sum(member["state"] == "merged" for member in members) == 46


def _ledger_rows(path: Path) -> dict[str, list[str]]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| ["):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            assert len(cells) == 12, line
            url = re.search(r"\]\((https://[^)]+)\)", cells[0]).group(1)
            rows[url] = cells
    return rows


@pytest.mark.parametrize("scores_path", SCORES, ids=lambda path: path.name)
def test_every_ledger_says_what_its_scores_say(scores_path: Path) -> None:
    document = json.loads(scores_path.read_text(encoding="utf-8"))
    members = CORPORA[document["corpus"]]
    scores = document["scores"]
    assert set(scores) == {member["slug"] for member in members}
    ledger = scores_path.with_name(scores_path.name.replace(".scores.json", ".md"))
    assert ledger.is_file(), f"{scores_path.name} has no ledger beside it"
    rows = _ledger_rows(ledger)
    assert len(rows) == len(members)

    mark = {True: "yes", False: "no"}
    counts = {level: 0 for level in LEVELS}
    for member in members:
        score = scores[member["slug"]]
        assert set(score) == {"answer_id", *LEVELS, "rationale"}, member["slug"]
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", score["answer_id"]), member["slug"]
        assert all(isinstance(score[level], bool) for level in LEVELS), member["slug"]
        assert score["rationale"].strip() and "|" not in score["rationale"], member["slug"]
        # Q2 is Q1 and more; Q1 is only scored on a relevant pull request.
        assert not score["q2"] or score["q1"], member["slug"]
        assert not score["q1"] or score["q0"], member["slug"]
        cells = rows[member["url"]]
        assert [cells[8], cells[9], cells[10]] == [mark[score[level]] for level in LEVELS], member["slug"]
        assert cells[11] == score["rationale"], member["slug"]
        if score["q1"]:
            # A Q1 answer states a change: an added, removed or changed row.
            assert sum(int(cells[index]) for index in (2, 3, 4)) > 0, member["slug"]
        for level in LEVELS:
            counts[level] += score[level]
    text = ledger.read_text(encoding="utf-8")
    total = len(members)
    expected = f"**Q0: {counts['q0']}/{total}. Q1: {counts['q1']}/{total}. Q2: {counts['q2']}/{total}**"
    assert expected in text


def test_the_2026_09_30_ledger_reproduces_the_published_counts() -> None:
    ledger = (BENCH / "results" / "2026-09-30-6ced6f70.md").read_text(encoding="utf-8")
    for line in (
        "1 `compared`, 42 `partial`, 6 `not_established`",
        "Pull requests with any row: 9 of 49",
        "**Q0: 37/49. Q1: 2/49. Q2: 1/49**",
    ):
        assert line in ledger
    rows = _ledger_rows(BENCH / "results" / "2026-09-30-6ced6f70.md")
    columns = {name: index for index, name in enumerate(("added", "removed", "changed", "not_established"), 2)}
    totals = {name: sum(int(cells[index]) for cells in rows.values()) for name, index in columns.items()}
    assert totals == {"added": 8, "removed": 0, "changed": 60, "not_established": 230}


def _write_run(out: Path, records: list[dict], outputs: dict[str, dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "runs.json").write_text(json.dumps(records), encoding="utf-8")
    for slug, payload in outputs.items():
        (out / f"{slug}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_the_counts_are_mechanical_and_the_levels_are_only_joined(tmp_path: Path) -> None:
    corpus = {"corpus": "toy", "members": [
        {"slug": slug, "repository": f"o/{slug}", "number": 1, "url": "u"} for slug in "abcde"
    ]}
    side = {"reach": {"calls": [{"method": "GET"}], "limits": []}}
    hop = {"reach": {"calls": [], "limits": [{"at": "x.py:1"}]}}
    answer_a = {"comparison_status": "compared", "comparison_id": "sha256:a", "rows": [
        {"change": "added", "before": None, "after": side},
        {"change": "changed", "before": hop, "after": hop},
    ]}
    answer_b = {"comparison_status": "partial", "comparison_id": "sha256:b", "rows": []}
    _write_run(
        tmp_path,
        [{"slug": "a", "status": "ok"}, {"slug": "b", "status": "ok"},
         {"slug": "c", "status": "refused"}, {"slug": "d", "status": "timeout"},
         {"slug": "e", "status": "unavailable"}],
        {
            "a": answer_a,
            "b": answer_b,
            # A stale file from an earlier run beside a refused member is never read.
            "c": {"comparison_status": "compared", "comparison_id": "sha256:old", "rows": []},
        },
    )
    members = summarize.load_run(corpus, tmp_path)
    scores = {
        "a": {"answer_id": summarize.answer_id(answer_a), "q0": True, "q1": True, "q2": False, "rationale": "x"},
        # Judged another answer than the one this run produced: stale.
        "b": {"answer_id": summarize.answer_id({**answer_b, "rows": [{"change": "added"}]}),
              "q0": True, "q1": True, "q2": True, "rationale": "y"},
    }
    totals = summarize.totals(members, scores)

    assert totals["status"] == {"compared": 1, "partial": 1, "refused": 1, "timeout": 1, "unavailable": 1}
    assert totals["members_with_rows"] == 1
    assert totals["rows"] == {"added": 1, "removed": 0, "changed": 1, "not_established": 0}
    assert totals["sides_with_calls"] == 1
    assert totals["sides_with_unresolved_hop"] == 2
    assert totals["stale_scores"] == ["b"]
    assert (totals["scored"], totals["q0"], totals["q1"], totals["q2"]) == (1, 1, 1, 0)

    with pytest.raises(SystemExit, match="runs.json"):
        summarize.load_run(corpus, tmp_path / "typo")


def test_a_score_survives_another_machine_or_release(tmp_path: Path) -> None:
    # ``comparison_id`` digests the engine's version, Python, platform and build:
    # the same answer read elsewhere keeps its score, and a different answer
    # does not (#926 review).
    corpus = {"corpus": "toy", "members": [{"slug": "a", "repository": "o/a", "number": 1, "url": "u"}]}
    answer = {"comparison_status": "compared", "rows": [{"change": "added"}],
              "engine": {"version": "1.2.0", "platform": "darwin"}, "comparison_id": "sha256:1"}
    score = {"answer_id": summarize.answer_id(answer), "q0": True, "q1": True, "q2": True, "rationale": "x"}
    elsewhere = {**answer, "engine": {"version": "1.3.0", "platform": "linux"}, "comparison_id": "sha256:2"}
    _write_run(tmp_path, [{"slug": "a", "status": "ok"}], {"a": elsewhere})
    assert summarize.totals(summarize.load_run(corpus, tmp_path), {"a": score})["q2"] == 1
    moved = {**elsewhere, "rows": [{"change": "changed"}]}
    _write_run(tmp_path, [{"slug": "a", "status": "ok"}], {"a": moved})
    totals = summarize.totals(summarize.load_run(corpus, tmp_path), {"a": score})
    assert (totals["q2"], totals["stale_scores"]) == (0, ["a"])


def test_the_readme_quotes_868s_levels_verbatim() -> None:
    for definition in (
        "**Q0 (relevant):** the PR changes an SDK/ADK agent's tools, handoffs or sub-agents in application code.",
        "**Q1 (correct and complete):** every binding change on the affected agents appears, with no false rows, and `compared` is never reported while a binding is unobservable.",
        "**Q2 (useful):** Q1, the PR modifies an agent that exists at the base, and each changed row names what the tool reaches (#872) or names the unresolved hop.",
        "**Any material omission or false row disqualifies the case.**",
    ):
        assert definition in README


def test_the_holdout_rule_is_frozen_by_its_digest() -> None:
    # Editing a definition or any step changes this digest; holdout.json records
    # the frozen one, so a change shows up here and in review rather than
    # silently. Step 4 reads "a binding change, as defined above", so the
    # definitions are frozen with the steps (#926 review).
    section = README[README.index("## Definitions"):README.index("## Every release")]
    assert "**A binding change**" in section and "## Holdout selection rule" in section
    assert hashlib.sha256(section.encode()).hexdigest() == HOLDOUT["rule_sha256"]
    # The pool is part of the rule: an edit to it is as visible as one to a step.
    assert hashlib.sha256((BENCH / "pool.json").read_bytes()).hexdigest() == HOLDOUT["pool_sha256"]
    assert HOLDOUT["window_start"] == "2026-10-02T00:00:00Z"
    assert "`2026-10-02T00:00:00Z`" in section
    assert HOLDOUT["target_members"] == 30 and "first 30 members" in section


def test_the_pool_was_recorded_with_every_request() -> None:
    assert POOL["queries"] == list(pool_script.QUERIES)
    assert POOL["size_shards"] == list(pool_script.SIZE_SHARDS)
    assert POOL["generated_at"] < "2026-10-03"
    assert POOL["repositories"] == sorted(set(POOL["repositories"]))
    seen = set()
    for request in POOL["requests"]:
        assert request["query"].startswith(tuple(POOL["queries"]))
        seen.update(request["repositories"])
    assert seen == set(POOL["repositories"]) and len(seen) > 100


def test_a_pinned_holdout_follows_its_rule() -> None:
    if HOLDOUT["status"] == "awaiting_window":
        assert HOLDOUT["members"] == [] and HOLDOUT["skipped"] == []
        return
    members = HOLDOUT["members"]
    assert len(members) == HOLDOUT["target_members"]
    development = {member["repository"] for member in DEVELOPMENT["members"]}
    walked = sorted([*members, *HOLDOUT["skipped"]], key=lambda item: (item["created_at"], item["url"]))
    assert [item["url"] for item in walked if item in members] == [item["url"] for item in members]
    for item in walked:
        assert item["reason"].strip()
        assert item["created_at"] >= HOLDOUT["window_start"]
        assert item["repository"] in POOL["repositories"]
        assert item["repository"] not in development
    for member in members:
        if member.get("unavailable_reason"):
            assert member["merge_base"] is None and member["head"] is None
        else:
            assert SHA.match(member["merge_base"]) and SHA.match(member["head"])


def test_every_release_after_1_2_0_records_the_count() -> None:
    runbook = (ROOT / "docs" / "release-runbook.md").read_text(encoding="utf-8")
    assert runbook.count("benchmark/application-q2") >= 1
    assert "`Q2: n/49 development, m/≥30 holdout`" in runbook
    advisory = runbook[runbook.index("## The advisory release channel"):]
    assert "application review count" in advisory

    def version(path: Path) -> tuple[int, ...]:
        return tuple(int(part) for part in path.stem.split("."))

    for record in sorted((ROOT / "docs" / "changelog").glob("*.md")):
        if version(record) > (1, 2, 0):
            text = record.read_text(encoding="utf-8")
            assert re.search(r"Q2: \d+/49 development", text), f"{record.name} lacks the Q2 line"


# -- the runner: a failed rerun never reports an earlier answer (#926 review) ------


def _runner_case(tmp_path: Path, slugs: tuple[str, ...]) -> tuple[dict, Path, Path, Path]:
    members = [
        {"slug": slug, "repository": f"o/{slug}", "number": 1,
         "merge_base": "a" * 40, "head": "b" * 40}
        for slug in slugs
    ]
    corpus = {"corpus": "toy", "members": members}
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps(corpus))
    clones, out = tmp_path / "clones", tmp_path / "out"
    for slug in slugs:
        (clones / f"o__{slug}" / ".git").mkdir(parents=True)
    engine = tmp_path / "engine"
    engine.write_text("")
    return corpus, corpus_path, clones, out


def _git_ok(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, 0, "", "")


@pytest.mark.parametrize("outcome", ["success", "refused", "timeout", "unavailable", "fetch_failed"])
def test_a_rerun_reports_current_outputs_and_fails_for_any_failed_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    corpus, corpus_path, clones, out = _runner_case(tmp_path, ("case",))
    if outcome == "fetch_failed":
        # No clone yet: ``--fetch`` has to make one, and the clone fails.
        (clones / "o__case" / ".git").rmdir()
    # An earlier run in the same directory, and a file of another corpus.
    _write_run(out, [{"slug": "case", "status": "ok"}],
               {"case": {"comparison_status": "compared", "rows": [{"change": "added"}]}})
    (out / "case.err").write_text("previous build diagnostic")
    (out / "other.json").write_text("unrelated output")
    current = {"comparison_status": "partial", "rows": [{"change": "removed"}]}
    engine_calls = []

    def fake_run(command, **kwargs):
        if command[0] == "git":
            if "clone" in command:
                return subprocess.CompletedProcess(command, 128, "", "fatal: repository not found")
            return _git_ok(command)
        engine_calls.append(command)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if outcome == "refused":
            return subprocess.CompletedProcess(command, 2, "", "current refusal")
        return subprocess.CompletedProcess(command, 0, json.dumps(current), "")

    monkeypatch.setattr(run, "complete", lambda clone, sha: outcome != "unavailable")
    monkeypatch.setattr(run.subprocess, "run", fake_run)
    args = ["--engine", str(tmp_path / "engine"), "--corpus", str(corpus_path),
            "--clones", str(clones), "--out", str(out)]
    if outcome == "fetch_failed":
        args.append("--fetch")

    exit_code = run.main(args)
    (record,) = json.loads((out / "runs.json").read_text())
    summary = summarize.load_run(corpus, out)["case"]

    assert exit_code == (0 if outcome == "success" else 1)
    assert (out / "other.json").read_text() == "unrelated output"
    assert summary["rows"]["added"] == 0
    assert "previous build" not in ((out / "case.err").read_text() if (out / "case.err").exists() else "")
    assert len(engine_calls) == (0 if outcome in {"unavailable", "fetch_failed"} else 1)
    if outcome == "success":
        assert (record["status"], summary["status"], summary["rows"]["removed"]) == ("ok", "partial", 1)
    else:
        expected = {"refused": "refused", "timeout": "timeout"}.get(outcome, "unavailable")
        assert record["status"] == summary["status"] == expected
        assert record["reason"]
        assert not (out / "case.json").exists()


@pytest.mark.parametrize("failure", ["refused", "timeout", "unavailable"])
def test_a_successful_member_does_not_hide_another_members_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    corpus, corpus_path, clones, out = _runner_case(tmp_path, ("good", "bad"))
    _write_run(out, [{"slug": "good", "status": "ok"}, {"slug": "bad", "status": "ok"}], {
        slug: {"comparison_status": "compared", "rows": [{"change": "added"}]} for slug in ("good", "bad")
    })

    def fake_run(command, **kwargs):
        if command[0] == "git":
            return _git_ok(command)
        # Every member's earlier answer is gone before the first engine starts.
        assert not (out / "bad.json").exists()
        if kwargs["cwd"].name == "o__bad" and failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        code = 2 if kwargs["cwd"].name == "o__bad" else 0
        return subprocess.CompletedProcess(command, code, json.dumps({"comparison_status": "partial", "rows": []}), "")

    monkeypatch.setattr(run, "complete", lambda clone, sha: clone.name != "o__bad" or failure != "unavailable")
    monkeypatch.setattr(run.subprocess, "run", fake_run)

    exit_code = run.main(["--engine", str(tmp_path / "engine"), "--corpus", str(corpus_path),
                          "--clones", str(clones), "--out", str(out), "--jobs", "1"])

    assert exit_code == 1
    statuses = {record["slug"]: record["status"] for record in json.loads((out / "runs.json").read_text())}
    assert statuses == {"good": "ok", "bad": failure}
    summary = summarize.load_run(corpus, out)
    assert summary["good"]["status"] == "partial"
    assert all(item["rows"]["added"] == 0 for item in summary.values())


def test_a_run_that_stops_early_leaves_nothing_to_summarize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, corpus_path, clones, out = _runner_case(tmp_path, ("case",))
    _write_run(out, [{"slug": "case", "status": "ok"}], {"case": {"comparison_status": "compared", "rows": []}})

    def interrupted(command, **kwargs):
        if command[0] == "git":
            return _git_ok(command)
        raise KeyboardInterrupt

    monkeypatch.setattr(run, "complete", lambda clone, sha: True)
    monkeypatch.setattr(run.subprocess, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run.main(["--engine", str(tmp_path / "engine"), "--corpus", str(corpus_path),
                  "--clones", str(clones), "--out", str(out)])
    with pytest.raises(SystemExit, match="runs.json"):
        summarize.load_run(corpus, out)


def test_a_git_timeout_while_pinning_makes_the_member_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, corpus_path, clones, out = _runner_case(tmp_path, ("case",))

    def slow_git(command, **kwargs):
        if command[0] == "git":
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 60))
        raise AssertionError("the engine must not run on unpinned commits")

    monkeypatch.setattr(run.subprocess, "run", slow_git)
    assert run.main(["--engine", str(tmp_path / "engine"), "--corpus", str(corpus_path),
                     "--clones", str(clones), "--out", str(out)]) == 1
    (record,) = json.loads((out / "runs.json").read_text())
    assert record["status"] == "unavailable"
    assert "timed out" in record["reason"]
