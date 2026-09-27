"""Measure widening markers on the frozen population and #820 controls.

Run with PYTHONPATH pointing to either candidate's src directory. Both runs
use this oracle and scorer; neither prior output nor engine decisions label
an expected direction. This isolated-file replay does not measure live reach.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path

import replay

SETTINGS = ".claude/settings.json"
HOOK = {"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "bin/lint.sh"}]}]}}
GUARDED = {"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "[ -f .skip-lint ] && exit 0; bin/lint.sh"}]}]}}
MCP = {"mcpServers": {"docs": {"command": "npx", "args": ["-y", "example-mcp-server@1.2.3"]}}}
READ_ONLY = {"mcpServers": {"docs": {"command": "npx", "args": ["-y", "example-mcp-server@1.2.3", "--read-only"]}}}
CONTROLS = {
    "guard": (SETTINGS, HOOK, GUARDED),
    "readonly": (".mcp.json", MCP, READ_ONLY),
    "disable": (SETTINGS, {"enabledPlugins": {"demo@local": True}}, {"enabledPlugins": {"demo@local": False}}),
    "addfalse": (SETTINGS, {}, {"enabledPlugins": {"demo@local": False}}),
    "mode": (SETTINGS, {"permissions": {"defaultMode": "bypassPermissions"}}, {"permissions": {"defaultMode": "default"}}),
    "sandbox": (SETTINGS, {"sandbox": {"enabled": False}}, {"sandbox": {"enabled": True}}),
    "enable": (SETTINGS, {"enabledPlugins": {"demo@local": False}}, {"enabledPlugins": {"demo@local": True}}),
    "bypass": (SETTINGS, {"permissions": {"defaultMode": "default"}}, {"permissions": {"defaultMode": "bypassPermissions"}}),
    "unsandbox": (SETTINGS, {"sandbox": {"enabled": True}}, {"sandbox": {"enabled": False}}),
    "addhook": (SETTINGS, {}, HOOK),
    "addmcp": (".mcp.json", {}, MCP),
    # A second handler on an event that already had one (#820 review).
    "addhandler": (SETTINGS, HOOK, {"hooks": {"PostToolUse": [
        *HOOK["hooks"]["PostToolUse"],
        {"matcher": "*", "hooks": [{"type": "command", "command": "bin/audit.sh"}]},
    ]}}),
}


def measure() -> dict:
    from agents_shipgate.core.verification_identity import _engine_distribution_sha256

    engine = _engine_distribution_sha256()
    score = replay._load("score")
    records = []
    for case_dir in replay.case_dirs():
        with tempfile.TemporaryDirectory() as tmp:
            record = replay.replay_record(case_dir, Path(tmp))
        records.append({"case": case_dir.name, "group": "population", **score.direction_counts(record)})
    for name, (path, before, after) in CONTROLS.items():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = root / "case"
            case.mkdir()
            (case / "case.json").write_text(json.dumps({"path": path, "kind": path}))
            (case / "base.txt").write_text(json.dumps(before))
            (case / "head.txt").write_text(json.dumps(after))
            record = replay.replay_record(case, root / "run")
        records.append({"case": name, "group": "control", **score.direction_counts(record)})
    totals = {}
    for group in ("population", "control"):
        count = Counter()
        for record in records:
            if record["group"] == group:
                count.update({key: value for key, value in record.items() if isinstance(value, int)})
        totals[group] = dict(count)
    if engine != _engine_distribution_sha256():
        raise RuntimeError("engine changed during direction replay; rerun on a stable candidate")
    return {"engine_distribution_sha256": engine, "totals": totals, "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = measure()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["totals"], indent=2))
