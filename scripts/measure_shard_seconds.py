"""Write ``tests/shard_seconds.json`` from a full-suite ``--junitxml`` report.

The CI suite is split into shards by measured time per test file
(``ci_sharding.py``). Re-measure when a shard nears its ``timeout-minutes``:

    python -m pytest -n auto -m "not perf" --ignore=tests/test_adapter_static_only.py \\
        --junitxml=junit.xml
    python scripts/measure_shard_seconds.py junit.xml

The times are relative weights: what matters is how the files compare with
each other, so one machine's measurement balances another's runners.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "tests" / "shard_seconds.json"


def file_seconds(report: Path, root: Path = REPO_ROOT, *, report_bytes: bytes | None = None) -> dict[str, float]:
    """Sum each mapped JUnit testcase time per test file."""

    totals: dict[str, float] = defaultdict(float)
    tree = ET.parse(report).getroot() if report_bytes is None else ET.fromstring(report_bytes)
    for case in tree.iter("testcase"):
        name = case.get("file") or _file_from_classname(case.get("classname", ""), root)
        if name:
            totals[name] += float(case.get("time") or 0.0)
    return {name: round(value, 1) for name, value in sorted(totals.items())}


def _file_from_classname(classname: str, root: Path) -> str | None:
    """``tests.test_x.TestY`` → ``tests/test_x.py``, the file the case is in."""

    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        candidate = root.joinpath(*parts[:end]).with_suffix(".py")
        if candidate.is_file():
            return candidate.relative_to(root).as_posix()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="a --junitxml report of the full CI suite")
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="the checkout the report was made in (default: this one)",
    )
    args = parser.parse_args(argv)
    report_bytes = args.report.read_bytes()
    files = file_seconds(args.report, args.root.resolve(), report_bytes=report_bytes)
    if not files:
        print(f"{args.report} holds no test cases", file=sys.stderr)
        return 1
    commit = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=args.root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    payload = {"measured_at": commit or None, "files": files, "measurement": {
        "source": "junit",
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "case_count": sum(1 for _ in ET.fromstring(report_bytes).iter("testcase")),
        "file_count": len(files),
        "time_basis": "JUnit testcase time; phase accounting is selected by the runner",
    }}
    OUTPUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(files)} files, {sum(files.values()):.0f}s in total, to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
