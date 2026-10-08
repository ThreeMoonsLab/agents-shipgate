#!/usr/bin/env python3
"""Phase-timing harness for ``agents-shipgate diff`` host mode (#698, #620).

Measurement only: it runs the same ``run_capability_diff`` the CLI runs, in a
fresh interpreter per sample, with the opt-in ``_perf`` instrumentation turned
on and the command's own output discarded. The command prints nothing extra in
normal use; the timings exist only in this harness's report.

    python scripts/measure_diff_phases.py --workspace <checkout> \\
        --base <merge-base-sha> --repeats 3

Each sample is one child process. ``startup_s`` is the child's wall clock minus
the time spent importing the CLI and running the command, so it is interpreter
start-up plus process teardown. Phases are inclusive and nested: a phase named
``a.b`` ran inside ``a`` only when ``a`` is its documented parent (see
``PARENTS``). ``exclusive`` subtracts a phase's children so the exclusive
column sums to the command's own time.

``--detect`` times, instead, the pair of walks ``detect_workspace`` makes
(#620): the host-boundary census (a filesystem walk) and the framework
candidate inventory (``git ls-files``), the latter of which ``init``,
``detect`` and ``diff --application`` pay on top of the former. Host-mode
``diff`` makes neither call. An exception from a walk is recorded as an
``errors`` entry, because a refusal is a measurement too.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

#: phase -> the phase it runs inside, for exclusive times. Phases absent from
#: this table are top-level.
PARENTS = {
    "diff.head_snapshot": "diff.run",
    "diff.resolve_base": "diff.run",
    "diff.base_materialize": "diff.run",
    "diff.base_snapshot": "diff.run",
    "diff.changed_inputs": "diff.run",
    "diff.compare": "diff.run",
    "diff.render": "diff.run",
    "archive.rev_parse_tree": "diff.base_materialize",
    "archive.copy_verified_graph": "diff.base_materialize",
    "archive.materialize_scoped": "diff.base_materialize",
    "archive.rescope": "diff.base_materialize",
    "archive.materialize_wider": "diff.base_materialize",
    "diff.base_dependency_snapshot": "archive.rescope",
    "archive.copy.pack_objects": "archive.copy_verified_graph",
    "archive.copy.index_pack": "archive.copy_verified_graph",
    "archive.copy.fsck": "archive.copy_verified_graph",
}


def _child(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    from agents_shipgate import _perf

    _perf.enable()
    import agents_shipgate.cli.main  # noqa: F401  (what the console script imports)

    imported = time.perf_counter()
    report: dict[str, object] = {"import_s": imported - started}
    sink = os.open(os.devnull, os.O_WRONLY)
    real_stdout = os.dup(1)
    os.dup2(sink, 1)
    try:
        if args.detect:
            from agents_shipgate.cli.discovery.artifacts import _candidate_files
            from agents_shipgate.cli.discovery.host_boundary import discover_host_boundary

            workspace = Path(args.workspace).resolve()
            marks: dict[str, object] = {}
            for name, call in (
                ("detect.host_boundary_walk", lambda: discover_host_boundary(workspace)),
                ("detect.framework_inventory_walk", lambda: _candidate_files(workspace)),
            ):
                t = time.perf_counter()
                try:
                    call()
                except Exception as exc:  # a refusal is a measurement too
                    marks[name + ".error"] = type(exc).__name__
                marks[name] = time.perf_counter() - t
            report["detect"] = marks
        else:
            import agents_shipgate.cli.diff as diff_module
            import agents_shipgate.cli.verify.host_tree as tree_module
            from agents_shipgate.cli.diff import run_capability_diff

            # Which side a snapshot read belongs to cannot be told from the
            # phase name, so each call to the reader is wrapped here (not in
            # the product) and its host.* phase deltas are kept under a label.
            reads: list[dict[str, object]] = []

            def tagged(module, label):
                original = module.build_host_boundary_snapshot

                def wrapper(*a, **kw):
                    before, t0 = _perf.snapshot(), time.perf_counter()
                    try:
                        return original(*a, **kw)
                    finally:
                        after = _perf.snapshot()
                        reads.append({
                            "label": label,
                            "total_s": time.perf_counter() - t0,
                            "phases": {
                                k: after[k] - before.get(k, 0.0)
                                for k in after
                                if k.startswith("host.")
                            },
                        })

                module.build_host_boundary_snapshot = wrapper

            tagged(diff_module, "diff")
            tagged(tree_module, "base_dependency")

            # The snapshot total is the sum of its two builds; record both
            # under one name so the table has one "inventory + read" row.
            t = time.perf_counter()
            exit_code = run_capability_diff(
                workspace=Path(args.workspace), base=args.base, json_output=True
            )
            report["run_s"] = time.perf_counter() - t
            report["exit_code"] = exit_code
            report["reads"] = reads
    finally:
        os.dup2(real_stdout, 1)
    phases = {k: v for k, v in _perf.snapshot().items() if not k.startswith("host.")}
    if "run_s" in report:
        phases["diff.run"] = report["run_s"]
    report["phases"] = phases
    report["counts"] = _perf.counts()
    sys.stdout.write(json.dumps(report) + "\n")


def _exclusive(phases: dict[str, float]) -> dict[str, float]:
    exclusive = dict(phases)
    for name, value in phases.items():
        parent = PARENTS.get(name)
        if parent in exclusive:
            exclusive[parent] -= value
    # archive.tree.* run inside both materializations (scoped, then wider), so
    # they are subtracted from the pair together.
    steps = sum(v for k, v in phases.items() if k.startswith("archive.tree."))
    exclusive["archive.materialize_other"] = (
        phases.get("archive.materialize_scoped", 0.0)
        + phases.get("archive.materialize_wider", 0.0)
        - steps
    )
    exclusive.pop("archive.materialize_scoped", None)
    exclusive.pop("archive.materialize_wider", None)
    return exclusive


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--base")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--detect", action="store_true", help="time the #620 walk pair instead of diff")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        _child(args)
        return 0

    samples: list[dict[str, object]] = []
    for _ in range(args.repeats):
        load = os.getloadavg()
        command = [sys.executable, __file__, "--child", "--workspace", args.workspace]
        if args.base:
            command += ["--base", args.base]
        if args.detect:
            command.append("--detect")
        started = time.perf_counter()
        done = subprocess.run(command, capture_output=True, text=True, check=False)
        wall = time.perf_counter() - started
        if done.returncode != 0:
            sys.stderr.write(done.stderr[-2000:])
            return done.returncode
        sample = json.loads(done.stdout.strip().splitlines()[-1])
        sample["wall_s"] = wall
        sample["loadavg_1m"] = load[0]
        samples.append(sample)

    summary: dict[str, object] = {
        "workspace": args.workspace,
        "base": args.base,
        "repeats": args.repeats,
        "loadavg_1m": [s["loadavg_1m"] for s in samples],
        "wall_s": [round(s["wall_s"], 3) for s in samples],
        "median_wall_s": round(_median([s["wall_s"] for s in samples]), 3),
    }
    if args.detect:
        names = sorted(n for n in samples[0]["detect"] if not n.endswith(".error"))
        summary["median"] = {
            name: round(_median([s["detect"][name] for s in samples]), 3) for name in names
        }
        summary["errors"] = {
            n: samples[0]["detect"][n] for n in samples[0]["detect"] if n.endswith(".error")
        }
    else:
        summary["exit_code"] = samples[0]["exit_code"]
        summary["median_import_s"] = round(_median([s["import_s"] for s in samples]), 3)
        summary["median_run_s"] = round(_median([s["run_s"] for s in samples]), 3)
        summary["median_startup_s"] = round(
            _median([s["wall_s"] - s["import_s"] - s["run_s"] for s in samples]), 3
        )
        per_sample = [_exclusive(s["phases"]) for s in samples]
        inclusive_names = sorted({name for s in samples for name in s["phases"]})
        exclusive_names = sorted({name for p in per_sample for name in p})
        summary["median_inclusive"] = {
            name: round(_median([s["phases"].get(name, 0.0) for s in samples]), 3)
            for name in inclusive_names
        }
        summary["median_exclusive"] = {
            name: round(_median([p.get(name, 0.0) for p in per_sample]), 3)
            for name in exclusive_names
        }
        # Snapshot reads, per labelled call (the head tree, then each base read).
        reads: dict[str, dict[str, list[float]]] = {}
        for sample in samples:
            for index, read in enumerate(sample["reads"]):
                key = f"{index}:{read['label']}"
                bucket = reads.setdefault(key, {"total_s": []})
                bucket["total_s"].append(read["total_s"])
                for phase, value in read["phases"].items():
                    bucket.setdefault(phase, []).append(value)
        summary["median_snapshot_reads"] = {
            key: {name: round(_median(values), 3) for name, values in sorted(bucket.items())}
            for key, bucket in sorted(reads.items())
        }
        summary["counts"] = samples[0]["counts"]
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
