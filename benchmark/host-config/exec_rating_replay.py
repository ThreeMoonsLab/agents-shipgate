"""Replay scoped Bash ratings on vendored settings heads and #824 controls.

Uses this checkout's oracle with either engine selected through PYTHONPATH.
This measures static declared-grant ratings, not live reach or PR additions.
"""
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

import replay

POSITIVE = [
    "python -c", "python3 -c", "node -e", "node --eval", "node -p", "node --print",
    "ruby -e", "perl -e", "perl -E", "php -r", "bash -c", "sh -c", "zsh -c", "pwsh -c",
    "eval", "npx", "bunx", "pnpm dlx", "pnpm exec", "uvx", "uv run", "uv tool run",
    "pipx run", "docker exec", "docker run", "xargs", "env", "sudo",
]
# Declarations wider than a launcher's own rule: never rated below it.
WIDER = [
    "python3 *", "python *", "python3:*", "node *", "bash *", "sh *", "docker *",
    "docker:*", "uv *", "pnpm *", "npx*", "python3 -c*", "env*", "n*",
]
NEGATIVE = [
    "npx prettier --check .", "npm test *", "find *", "make *", "sed *", "gh api *",
    "python3 -m pytest *", "node server.js *", "ruby -c *", "bash -e *", "python -e *",
    "npx prettier *", "env CI=1 npm test *", "xargs grep *", "docker inspect *",
    "n *", "npm *", "python3 -c foo *",
]


def measure() -> dict:
    from agents_shipgate.core.host_grants import host_audit_inventory
    from agents_shipgate.core.verification_identity import _engine_distribution_sha256

    digest = _engine_distribution_sha256()
    oracle = replay._load("expected")
    cases = []
    for directory in replay.case_dirs():
        case = json.loads((directory / "case.json").read_text())
        if case["path"] in {".claude/settings.json", ".claude/settings.local.json"}:
            path = directory / "head.txt"
            if path.exists():
                cases.append((directory.name, "population", case["path"], path.read_text()))
    rules = [f"Bash({p}{s})" for p in POSITIVE for s in (" *", ":*")]
    rules += [f"Bash({p})" for p in WIDER + NEGATIVE]
    cases.append(("launcher-controls", "control", ".claude/settings.json", json.dumps({"permissions": {"allow": rules}})))
    records = []
    for name, group, path, body in cases:
        declarations = json.loads(body).get("permissions", {}).get("allow", [])
        expected = {r: oracle.scoped_bash_rating(r) for r in declarations if isinstance(r, str)}
        expected = {r: v for r, v in expected.items() if v is not None}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / path
            target.parent.mkdir(parents=True)
            target.write_text(body)
            inventory = host_audit_inventory(root)
        actual = {g["rule"]: g["risk"] for g in inventory["grants"] if g.get("disposition") == "allow" and g.get("source") == path}
        marked = {r for r in expected if actual.get(r) == "critical"}
        positive = {r for r, risk in expected.items() if risk == "critical"}
        records.append({
            "case": name, "group": group, "cases": 1, "scored_rules": len(expected),
            "expected_critical": len(positive), "marked_critical": len(marked),
            "true_critical": len(marked & positive), "false_critical": len(marked - positive),
            "missed_critical": len(positive - marked),
            "missing_rules": len(set(expected) - set(actual)),
            "correct_ratings": sum(actual.get(r) == risk for r, risk in expected.items()),
        })
    totals = {}
    for group in ("population", "control"):
        count = Counter()
        for record in records:
            if record["group"] == group:
                count.update({k: v for k, v in record.items() if isinstance(v, int)})
        totals[group] = dict(count)
    if digest != _engine_distribution_sha256():
        raise RuntimeError("engine changed during rating replay")
    return {"engine_distribution_sha256": digest, "totals": totals, "records": records}


if __name__ == "__main__":
    result = measure()
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["totals"], indent=2))
