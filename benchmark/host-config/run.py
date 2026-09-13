"""#659 driver: fetch each PR's merge commit and parent, run `shipgate diff`, record everything.

Not a cold-start measurement (that is #660): the question here is whether the
rows are right. Each case fetches exactly two commits (`--depth 2` at the merge
commit), checks out the merge commit, and compares against its first parent,
the convention `benchmark/miner` uses. The candidate is installed once.

An incomparable comparison also records `audit --host --json` at head and at
base. `shipgate diff --json` names no per-source limit, and a refusal is only
useful once it is mapped to the limit, and the issue, behind it.

    python benchmark/host-config/run.py <selection.json> <wheel> <workdir> <results dir>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expected  # noqa: E402

RECORD_KEYS = ("repo", "kind", "path", "pr_number", "merge_commit_sha", "coding_agent_marked")


def run(args: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[int, str, str, float]:
    started = time.monotonic()
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", "timeout", round(time.monotonic() - started, 2)
    return result.returncode, result.stdout, result.stderr, round(time.monotonic() - started, 2)


def show(repo_dir: Path, sha: str, path: str) -> str | None:
    code, out, _, _ = run(["git", "show", f"{sha}:{path}"], cwd=repo_dir)
    return out if code == 0 else None


def audit_sides(shipgate: str, repo_dir: Path, base: str) -> dict[str, dict]:
    sides: dict[str, dict] = {}
    for side, ref in (("head", "HEAD"), ("base", base)):
        run(["git", "checkout", "-q", ref], cwd=repo_dir)
        code, out, _, _ = run(
            [shipgate, "audit", "--host", "--json", "--workspace", str(repo_dir)], cwd=repo_dir, timeout=300
        )
        try:
            audit = json.loads(out)
        except json.JSONDecodeError:
            sides[side] = {"exit": code, "unparsed": out[-300:]}
            continue
        sides[side] = {"issues": audit.get("issues"), "host_coverage": audit.get("host_coverage")}
    return sides


def main(selection_path: Path, wheel: Path, work: Path, out: Path) -> int:
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    venv = work / "candidate.venv"
    if not (venv / "bin" / "shipgate").exists():
        run([sys.executable, "-m", "venv", str(venv)])
        code, _, err, _ = run([str(venv / "bin" / "python"), "-m", "pip", "install", "-q", str(wheel)], timeout=900)
        if code:
            raise SystemExit(f"install failed: {err[-300:]}")
    shipgate = str(venv / "bin" / "shipgate")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    records = []
    for case in selection["accepted"]:
        slug = f"{case['repo'].replace('/', '__')}__pr{case['pr_number']}"
        repo_dir = work / slug
        shutil.rmtree(repo_dir, ignore_errors=True)
        repo_dir.mkdir()
        record: dict = {"case": slug, **{key: case[key] for key in RECORD_KEYS}}
        steps = [
            ["git", "init", "-q"],
            ["git", "fetch", "-q", "--depth", "2", f"https://github.com/{case['repo']}.git", case["merge_commit_sha"]],
            ["git", "checkout", "-q", "FETCH_HEAD"],
        ]
        failed = None
        for step in steps:
            code, _, err, _ = run(step, cwd=repo_dir, timeout=900)
            if code:
                failed = f"{step[1]}: {err[-200:]}"
                break
        if failed:
            records.append({**record, "stop_point": "fetch_failed", "detail": failed})
            print(f"{slug}: fetch failed")
            continue
        code, base, _, _ = run(["git", "rev-parse", "HEAD^1"], cwd=repo_dir)
        if code:
            records.append({**record, "stop_point": "parent_unavailable"})
            print(f"{slug}: no parent")
            continue
        base = base.strip()
        record["base_sha"] = base
        record["expectation"] = expected.expected(
            case["kind"], show(repo_dir, base, case["path"]), show(repo_dir, "HEAD", case["path"])
        )
        code, stdout, stderr, elapsed = run(
            [shipgate, "diff", "--workspace", str(repo_dir), "--base", base, "--json"], timeout=300
        )
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
        if code == 124:
            stop_point = "comparison_timeout"
        elif payload is None:
            stop_point = "comparison_output_unparsed"
        else:
            stop_point = f"comparison_{payload.get('comparison_status')}"
        record.update(payload=payload, comparison_seconds=elapsed, stderr_tail=stderr[-400:], stop_point=stop_point)
        if stop_point == "comparison_incomparable":
            record["audit"] = audit_sides(shipgate, repo_dir, base)
        records.append(record)
        rows = len((payload or {}).get("rows") or [])
        print(f"{slug}: {stop_point} rows={rows} exp={len(record['expectation']['changes'])} scope={record['expectation']['scope']}", flush=True)
        shutil.rmtree(repo_dir, ignore_errors=True)
    document = {"selection_date": selection["selection_date"], "wheel": wheel.name, "records": records}
    (out / "runs.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(records)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*(Path(arg).resolve() for arg in sys.argv[1:5])))
