"""Replay vendored #659 cases offline against this tree's engine.

Each case is rebuilt as a two-commit repository, the selected file at its
pinned base and then at its merge commit, and compared with `shipgate diff`,
the command the live run issues. `score.py` scores the result against the
expectation `expected.py` derives from the two files alone.

One implementation, two callers: `tests/test_host_config_replay.py` asserts
each case still replays to its committed `replay.json`, and

    python benchmark/host-config/replay.py --record

rewrites those files, so an outcome only changes in a reviewed diff.

A vendored case holds only the selected file. This pins comparison correctness
on real-world shapes, not reach: the live run's refusals came from elsewhere in
each repository, and the population rates are in `results/`, never inferred
from a replay.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
OBSERVED_KEYS = (
    "stop_point", "scope", "expected_changes", "expected_widenings", "rows_on_file",
    "correct_rows", "widenings_named", "benign_case", "benign_zero_row",
    "incomparable_reasons", "unclassified_keys",
)
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "replay", "GIT_AUTHOR_EMAIL": "replay@example.invalid",
    "GIT_COMMITTER_NAME": "replay", "GIT_COMMITTER_EMAIL": "replay@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _load(name: str) -> ModuleType:
    # Unique module names: the cold-start harness imports modules of the same names.
    spec = importlib.util.spec_from_file_location(f"host_config_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _commit(repo: Path, path: str, text: str | None, message: str) -> str:
    target = repo / path
    if text is None:
        target.unlink(missing_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def case_dirs() -> list[Path]:
    return sorted(path for path in CASES.iterdir() if path.is_dir()) if CASES.is_dir() else []


def replay_case(case_dir: Path, workdir: Path) -> dict[str, Any]:
    """The observed outcome of one vendored case, in `replay.json`'s shape."""

    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    expected = _load("expected")
    score = _load("score")
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    sides = {
        side: (case_dir / f"{side}.txt").read_bytes().decode("utf-8")
        if (case_dir / f"{side}.txt").exists()
        else None
        for side in ("base", "head")
    }
    repo = workdir / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, case["path"], sides["base"], "base")
    _commit(repo, case["path"], sides["head"], "head")

    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", base, "--json"])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = None
    record = {
        "case": case_dir.name,
        "kind": case["kind"],
        "path": case["path"],
        "coding_agent_marked": case.get("coding_agent_marked"),
        "payload": payload,
        "stop_point": (
            "comparison_output_unparsed"
            if payload is None
            else f"comparison_{payload.get('comparison_status')}"
        ),
        "expectation": expected.expected(case["kind"], sides["base"], sides["head"]),
    }
    outcome = score.score_record(record)
    return {key: outcome[key] for key in OBSERVED_KEYS}


def main(argv: list[str]) -> int:
    record = "--record" in argv
    changed = 0
    for case_dir in case_dirs():
        with tempfile.TemporaryDirectory() as tmp:
            observed = replay_case(case_dir, Path(tmp))
        target = case_dir / "replay.json"
        recorded = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        if observed != recorded:
            changed += 1
            print(f"{'recorded' if record else 'DIFFERS'} {case_dir.name}: {observed['stop_point']} rows={observed['rows_on_file']} expected={observed['expected_changes']}")
            if record:
                target.write_text(json.dumps(observed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(case_dirs())} cases, {changed} {'recorded' if record else 'differ from replay.json'}")
    return 0 if record or not changed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
