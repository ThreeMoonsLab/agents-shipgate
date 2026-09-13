"""Live cold-start driver for the frozen #660 population.

For each selected case, from nothing:

1. a fresh clone of the public repository (full history, the default a user
   gets from `git clone`), checked out at the pinned head;
2. the documented installation route for a downloaded build — a fresh virtual
   environment and `pip install ./<wheel>` — timed and counted separately;
3. the comparison: `shipgate diff --workspace <clone> --base <base> --json`.
   A recovery command the CLI prints is followed and counted; nothing else is
   run, no manifest, baseline, policy or fetch is added by this driver.

It records what happened, not whether it was right: `score.py` compares the
rows with the expectation fixed by `expected.py` before this runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import expected as expectation  # noqa: E402

SELECTION, WHEEL, WORK, OUT = (Path(arg) for arg in sys.argv[1:5])
COMPARISON_BUDGET_SECONDS = 300


def run(args: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[int, str, str, float]:
    started = time.monotonic()
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        code, out, err = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        code, out, err = 124, exc.stdout or "", f"timeout after {timeout}s"
    return code, out if isinstance(out, str) else out.decode(), err, round(time.monotonic() - started, 2)


def file_at(clone: Path, sha: str, path: str) -> str | None:
    code, out, _, _ = run(["git", "-C", str(clone), "show", f"{sha}:{path}"])
    return out if code == 0 else None


selection = json.loads(SELECTION.read_text())
OUT.mkdir(parents=True, exist_ok=True)
records = []
for case in selection["accepted"]:
    repo, kind, head, base = case["repo"], case["kind"], case["head_sha"], case["base_sha"]
    slug = f"{repo.replace('/', '__')}__{kind.strip('.').replace('/', '_')}"
    clone = WORK / slug
    venv = WORK / f"{slug}.venv"
    for path in (clone, venv):
        shutil.rmtree(path, ignore_errors=True)
    record: dict = {"case": slug, "repo": repo, "kind": kind, "base_sha": base, "head_sha": head, "commands": []}

    code, _, err, elapsed = run(["git", "clone", "-q", f"https://github.com/{repo}.git", str(clone)])
    record["clone_seconds"] = elapsed
    if code:
        record.update(stop_point="clone_failed", detail=err[-300:])
        records.append(record)
        print(f"{slug}: clone failed", flush=True)
        continue
    code, _, err, _ = run(["git", "-C", str(clone), "checkout", "-q", head])
    if code:
        record.update(stop_point="head_unavailable", detail=err[-300:])
        records.append(record)
        print(f"{slug}: head unavailable", flush=True)
        continue
    record["expectation"] = expectation.expected(kind, file_at(clone, base, kind), file_at(clone, head, kind))

    install_started = time.monotonic()
    install = [
        [sys.executable, "-m", "venv", str(venv)],
        [str(venv / "bin" / "python"), "-m", "pip", "install", "-q", str(WHEEL)],
    ]
    install_failed = None
    for args in install:
        code, _, err, _ = run(args, timeout=900)
        if code:
            install_failed = err[-300:]
            break
    record["install_commands"] = len(install)
    record["install_seconds"] = round(time.monotonic() - install_started, 2)
    if install_failed:
        record.update(stop_point="install_failed", detail=install_failed)
        records.append(record)
        print(f"{slug}: install failed", flush=True)
        continue

    shipgate = str(venv / "bin" / "shipgate")
    command = [shipgate, "diff", "--workspace", str(clone), "--base", base, "--json"]
    comparison_started = time.monotonic()
    code, out, err, elapsed = run(command, timeout=COMPARISON_BUDGET_SECONDS)
    record["commands"].append({"argv": ["shipgate", *command[1:]], "exit": code, "seconds": elapsed})
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = None
    record["comparison_seconds"] = round(time.monotonic() - comparison_started, 2)
    record["payload"] = payload
    record["stderr_tail"] = err[-600:]
    record["stop_point"] = (
        "comparison_timeout" if code == 124
        else "comparison_output_unparsed" if payload is None
        else f"comparison_{payload.get('comparison_status')}"
    )
    records.append(record)
    rows = len((payload or {}).get("rows") or [])
    print(f"{slug}: {record['stop_point']} rows={rows} {record['comparison_seconds']}s scope={record['expectation']['scope']}", flush=True)
    shutil.rmtree(venv, ignore_errors=True)

(OUT / "runs.json").write_text(json.dumps({"selection_date": selection["selection_date"], "wheel": WHEEL.name, "records": records}, indent=2) + "\n")
print(f"wrote {len(records)} records")
