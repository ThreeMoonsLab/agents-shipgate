"""Live driver for the #658 ten-server false-finding table.

For each server in `selection.json`, from nothing:

1. a clone of the public repository fetched at its pinned commit;
2. one bound `mcp_server_source` manifest over the selected route, the same
   shape a maintainer adopting the server would write, with nothing else;
3. `shipgate scan --config <manifest> --format json` from a fresh virtual
   environment with `pip install ./<wheel>`.

It records what the scan published, not whether it was right: every finding
is judged in `labels.csv`, and `score.py` joins the two.

    python benchmark/mcp-servers/run.py benchmark/mcp-servers/selection.json ./agents_shipgate-<version>-py3-none-any.whl /tmp/mcp-servers benchmark/mcp-servers/results/<date>-<candidate>
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "cold-start"))
from vendor import mask_local_paths  # noqa: E402

MANIFEST = """version: "0.1"
project:
  name: {server}
agent:
  name: {server}-evaluation
  declared_purpose:
    - exercise every tool the server registers
environment:
  target: local
tool_sources:
  - id: server
    type: mcp_server_source
    path: {route}
    binding:
      complete: true
      reason: This repository is the server; every registered tool is callable by any connected client.
ci:
  mode: advisory
"""


def run(args: list[str], cwd: Path | None = None, timeout: int = 1800) -> tuple[int, str, str, float]:
    started = time.monotonic()
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        code, out, err = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        code, out, err = 124, "", f"timeout after {timeout}s"
    return code, out, err, round(time.monotonic() - started, 2)


#: Run with the candidate's own interpreter, so the counts come from the build
#: under test. The report publishes neither per-tool extraction nor why a
#: description is absent, so the reader is asked directly.
READER_COVERAGE = """
import json, sys
from pathlib import Path
from agents_shipgate.inputs.mcp_server_source import load_mcp_server_source
from agents_shipgate.schemas.manifest import ToolSourceConfig
loaded = load_mcp_server_source(ToolSourceConfig(id="server", type="mcp_server_source", path=sys.argv[2]), Path(sys.argv[1]))
extraction = [tool.extraction or {} for tool in loaded.tools]
print(json.dumps({
    "descriptions_unresolved": sum(item.get("description") == "unresolved" for item in extraction),
    "annotations_unresolved": sum(item.get("annotations") == "unresolved" for item in extraction),
    "surface_partial": sum(item.get("surface") == "partial" for item in extraction),
}))
"""


def coverage(report: dict, python: Path, clone: Path, route: str) -> dict:
    """What the reader could not establish, reported beside the rate (#658)."""

    counts: dict = {"unenumerated_registrations": (report.get("surface_exclusions") or {}).get("total")}
    code, out, err, _seconds = run([str(python), "-c", READER_COVERAGE, str(clone), route])
    if code == 0 and out.strip():
        counts.update(json.loads(out.strip().splitlines()[-1]))
    else:
        counts["reader_error"] = err.strip()[-300:]
    return counts


def finding_rows(report: dict) -> list[dict]:
    catalog = {tool["name"]: tool for tool in report.get("tool_catalog") or []}
    rows = []
    for finding in report.get("findings") or []:
        if finding.get("suppressed"):
            continue
        tool = catalog.get(finding.get("tool_name") or "", {})
        rows.append(
            {
                "check_id": finding["check_id"],
                "tool": finding.get("tool_name") or "",
                "source_path": tool.get("source_path") or "",
                "line": tool.get("source_start_line"),
                "severity": finding.get("severity"),
                "policy_eligible": (finding.get("support") or {}).get("policy_eligible"),
                "evidence": finding.get("evidence"),
            }
        )
    return sorted(rows, key=lambda row: (row["check_id"], row["tool"], row["source_path"]))


def main(selection_path: Path, wheel: Path, work: Path, out: Path) -> int:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    venv = work / "candidate.venv"
    shutil.rmtree(venv, ignore_errors=True)
    for command in ([sys.executable, "-m", "venv", str(venv)], [str(venv / "bin" / "pip"), "install", "-q", str(wheel)]):
        code, _out, err, _seconds = run(command)
        if code != 0:
            print(err, file=sys.stderr)
            return 1
    cli = str(venv / "bin" / "shipgate")
    records = []
    for server in selection["accepted"]:
        clone = work / f"{server['repo'].replace('/', '__')}@{server['commit'][:12]}"
        if not (clone / ".git").is_dir():
            shutil.rmtree(clone, ignore_errors=True)
            clone.mkdir(parents=True)
            for command in (
                ["git", "init", "-q"],
                ["git", "remote", "add", "origin", f"https://github.com/{server['repo']}.git"],
                ["git", "fetch", "-q", "--depth", "1", "origin", server["commit"]],
                ["git", "checkout", "-q", "--detach", "FETCH_HEAD"],
            ):
                code, _out, err, _seconds = run(command, cwd=clone)
                if code != 0:
                    records.append({"server": server["server"], "error": f"clone failed: {err.strip()[:300]}"})
                    break
            else:
                pass
            if records and records[-1].get("server") == server["server"] and "error" in records[-1]:
                continue
        manifest = clone / f"shipgate-mcp-servers-{server['server']}.yaml"
        manifest.write_text(MANIFEST.format(server=server["server"], route=server["route"]), encoding="utf-8")
        reports = work / "reports" / server["server"]
        shutil.rmtree(reports, ignore_errors=True)
        code, _out, err, seconds = run(
            [cli, "scan", "--config", str(manifest), "--out", str(reports), "--format", "json", "--no-packet"], cwd=clone
        )
        record = {"server": server["server"], "repo": server["repo"], "commit": server["commit"], "route": server["route"], "exit": code, "seconds": seconds}
        report_path = reports / "report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            record["tools"] = len(report.get("tool_catalog") or [])
            record["coverage"] = coverage(report, venv / "bin" / "python", clone, server["route"])
            record["findings"] = finding_rows(report)
        else:
            record["error"] = err.strip()[-500:]
        records.append(record)
        print(f"{server['server']}: exit={code} tools={record.get('tools')} findings={len(record.get('findings') or [])} {seconds}s")
    payload = {
        "selection_frozen_at": selection["frozen_at"],
        "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "records": records,
    }
    (out / "runs.json").write_text(mask_local_paths(json.dumps(payload, indent=2, sort_keys=True)) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*(Path(arg).resolve() for arg in sys.argv[1:5])))
