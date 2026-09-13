"""Replay vendored cold-start cases offline against this tree's engine (#660).

Each case is rebuilt as a two-commit repository — the selected file at its
pinned base, then at its pinned head — and compared with `shipgate diff`, the
comparison command the live run issues. The result is scored by `score.py`
against an expectation `expected.py` derives from the files alone.

One implementation, two callers: `tests/test_cold_start_replay.py` asserts
each case still replays to its committed `replay.json`, and

    python benchmark/cold-start/replay.py --record

rewrites those files. Recording is explicit, so an outcome only changes in a
reviewed diff.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
OBSERVED_KEYS = (
    "scope", "stop_point", "expected_changes", "rows_on_file", "missing",
    "unexpected", "success", "missing_detail", "unexpected_detail",
)
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "replay", "GIT_AUTHOR_EMAIL": "replay@example.invalid",
    "GIT_COMMITTER_NAME": "replay", "GIT_COMMITTER_EMAIL": "replay@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _import_sibling(name: str) -> Any:
    sys.path.insert(0, str(HERE))
    try:
        return __import__(name)
    finally:
        sys.path.remove(str(HERE))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=_GIT_ENV
    ).stdout.strip()


def _commit(repo: Path, kind: str, text: str | None, message: str) -> str:
    target = repo / kind
    if text is None:
        target.unlink(missing_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def case_dirs() -> list[Path]:
    return sorted(path for path in CASES.iterdir() if path.is_dir()) if CASES.is_dir() else []


def replay_case(case_dir: Path, workdir: Path) -> dict[str, Any]:
    """The observed outcome of one vendored case, in `replay.json`'s shape."""

    from typer.testing import CliRunner

    from agents_shipgate.cli.main import app

    expected = _import_sibling("expected")
    score = _import_sibling("score")
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    sides = {
        side: (case_dir / f"{side}.txt").read_text(encoding="utf-8")
        if (case_dir / f"{side}.txt").exists()
        else None
        for side in ("base", "head")
    }
    repo = workdir / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, case["kind"], sides["base"], "base")
    _commit(repo, case["kind"], sides["head"], "head")

    result = CliRunner().invoke(app, ["diff", "--workspace", str(repo), "--base", base, "--json"])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = None
    record = {
        "case": case_dir.name, "repo": case["repo"], "kind": case["kind"],
        "base_sha": case["base_sha"], "head_sha": case["head_sha"],
        "commands": [{"exit": result.exit_code}],
        "comparison_seconds": 0.0,
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
            print(f"{'recorded' if record else 'DIFFERS'} {case_dir.name}: success={observed['success']} {observed['stop_point']}")
            if record:
                target.write_text(json.dumps(observed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(case_dirs())} cases, {changed} {'recorded' if record else 'differ from replay.json'}")
    return 0 if record or not changed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
