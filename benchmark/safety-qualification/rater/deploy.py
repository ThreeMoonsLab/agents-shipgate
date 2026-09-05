"""Lay out a rater deployment that carries no answer.

A corpus rater session for the openai family runs with a shell, and
``--sandbox read-only`` restricts writes rather than reads, so the thing that
keeps it blind is not a sandbox — it is that the answers are not on the host
to be read. This builds the host.

What goes in::

    <deploy>/
      src/agents_shipgate/     the harness imports from it, and nothing else
      benchmark/safety-qualification/rater/{build_packet,run_rater}.py
      packets/<case>.<role>/   whatever packets were handed to `--packets`
      runs/                    where the labels and transcripts land

What stays out is the point: no ``strata-inventory``, no
``benchmark/miner/results/*.labels.csv``, no calibration record. The layout is
not cosmetic — ``run_rater`` finds its root three directories above itself, so
``answer_keys_on_host()`` looks *here*, and this script refuses to finish if it
finds anything.

**Packets are built elsewhere, on purpose.** Building one needs the case's
clone or its constructed tree, and choosing *which* to build needs the
inventory, which is the answer. So the split is the isolation: the machine
that decides what to build may see the inventory, and the deployment that runs
the raters may not.

Usage::

    python benchmark/safety-qualification/rater/deploy.py \\
        --out ~/cut-c-host --packets /path/to/packets
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

RATER_DIR = Path(__file__).resolve().parent
REPO_ROOT = RATER_DIR.parents[2]
HARNESS_FILES = ("build_packet.py", "run_rater.py", "__init__.py")


def _load_run_rater():
    spec = importlib.util.spec_from_file_location("rater_run_rater", RATER_DIR / "run_rater.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DeployError(RuntimeError):
    """The deployment cannot be laid out as asked."""


def deploy(out: Path, packets: Path | None = None) -> list[Path]:
    """Build the deployment at ``out``; returns any answer files found in it.

    An empty list is the whole point. A non-empty one means this script laid
    out something a rater must not have, and the caller should treat it as a
    failure rather than a warning.
    """

    if out.exists() and any(out.iterdir()):
        raise DeployError(f"{out} exists and is not empty; deploy to a fresh path")
    rater_out = out / "benchmark" / "safety-qualification" / "rater"
    rater_out.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "src" / "agents_shipgate", out / "src" / "agents_shipgate")
    for name in HARNESS_FILES:
        source = RATER_DIR / name
        if source.is_file():
            shutil.copyfile(source, rater_out / name)
    if packets is not None:
        if not packets.is_dir():
            raise DeployError(f"{packets} is not a directory")
        shutil.copytree(packets, out / "packets")
    (out / "runs").mkdir(exist_ok=True)

    run_rater = _load_run_rater()
    return run_rater.answer_keys_on_host(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path, help="deployment root; must be empty")
    parser.add_argument("--packets", type=Path, help="directory of built packets to copy in")
    args = parser.parse_args(argv)
    try:
        leaked = deploy(args.out.expanduser(), args.packets)
    except DeployError as error:
        print(f"deploy: {error}", file=sys.stderr)
        return 2
    if leaked:
        print(
            "deploy: refused — the deployment carries files that state an answer: "
            + ", ".join(str(path) for path in leaked),
            file=sys.stderr,
        )
        return 2
    out = args.out.expanduser()
    print(f"deployment: {out}")
    print("answer files on this host: none")
    print()
    print("Run a corpus session from it — no --working-material, and it will refuse")
    print("if an answer file ever appears:")
    print(f"  cd {out}")
    print("  python benchmark/safety-qualification/rater/run_rater.py \\")
    print("    --family openai --role framework_tooling --model <model> \\")
    print("    --packet packets/<case>.framework_tooling --out runs --home-mode shared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
