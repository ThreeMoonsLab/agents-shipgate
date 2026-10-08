"""Write and check the reviewer-first text goldens of ``diff --application`` (#914).

Usage:
    PYTHONPATH=src python benchmark/application-q2/goldens.py \
        --out ~/.cache/shipgate-q2/out-<build> [--write]

Reads a ``run.py`` output directory and, for each member in ``GOLDEN_MEMBERS``,
renders the text the CLI prints from that run's JSON. It compares the text with
``goldens/<slug>.txt`` and ``--write`` replaces it. Beside each text is
``goldens/<slug>.input.json.gz``: the answer with every field neither the
summary nor the printed detail reads removed. ``tests/test_application_summary.py``
renders that input in CI, where the corpus clones do not exist, and holds it to
the text. Generation refuses a member whose own ``summary`` block is not the one
the code builds from its rows, and one whose reduced input renders differently
from the whole answer.

Exit 0: the goldens match (or were written). Exit 1: a golden differs.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import gzip
import io
import json
import sys
from pathlib import Path

from agents_shipgate.cli.application_diff import _print_comparison
from agents_shipgate.cli.application_summary import DETAIL_ROW_LIMIT, build_summary

HERE = Path(__file__).resolve().parent
GOLDENS = HERE / "goldens"

#: #914's three named cases, then one that reaches a process with
#: model-controlled arguments, one tool object, and one answer spread over
#: eight agents.
GOLDEN_MEMBERS = (
    "jpka_attest-3",
    "Tiendat2703_MIS_TALENT-7",
    "VidulaWickramasinghe_O.R.I.O.N-126",
    "xju2_hepagent-72",
    "buriro-ezekia_cinescout-ai-9",
    "cianfhoghlaim_tuatha-1",
)

#: What the summary reads of a side, and nothing else of it.
_SIDE_KEYS = (
    "agent_source",
    "binding_location",
    "signature",
    "input_schema",
    "output_schema",
    "bound_when",
    "object",
    "reach",
    "effect_evidence",
)


def render(payload: dict) -> str:
    """The text the CLI prints for an answer."""

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        _print_comparison(payload, payload["base"]["compared_commit"], payload["head"]["compared_commit"])
    return buffer.getvalue()


def reduce(payload: dict) -> dict:
    """The answer without what neither the summary nor the printed text reads."""

    result = copy.deepcopy(payload)
    for key in ("engine", "options", "source_correspondence"):
        result.pop(key, None)
    for side in ("base", "head"):
        data = result[side]
        result[side] = {
            key: data[key]
            for key in ("compared_commit", "scope", "limits", "coverage_gaps", "excluded_tests")
            if key in data
        }
    selection = result.get("scope_selection")
    if selection:
        result["scope_selection"] = {
            key: selection[key] for key in ("mode", "scopes", "reason", "limits") if key in selection
        }
    if len(result["rows"]) > DETAIL_ROW_LIMIT:
        # The rows are not printed above the limit, so only what the summary
        # reads of each survives.
        rows = []
        for row in result["rows"]:
            slim = {
                key: row[key]
                for key in ("agent", "agent_source", "tool", "change", "candidate_change", "uncertainty")
            }
            for side in ("before", "after"):
                value = row[side]
                slim[side] = (
                    None
                    if value is None
                    else {
                        **{key: value[key] for key in _SIDE_KEYS if key in value},
                        "definition": {
                            "implementation_sha256": (value.get("definition") or {}).get("implementation_sha256")
                        },
                    }
                )
            rows.append(slim)
        result["rows"] = rows
    return result


def encode(payload: dict) -> bytes:
    """Deterministic gzip of the reduced answer, its keys in the run's own order.

    A tool's arguments are named in the order its schema declares them, so a
    reordered object is a different (alphabetical) summary.
    """

    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return gzip.compress(raw, compresslevel=9, mtime=0)


def build(out: Path) -> dict[str, tuple[str, bytes]]:
    made: dict[str, tuple[str, bytes]] = {}
    for slug in GOLDEN_MEMBERS:
        path = out / f"{slug}.json"
        if not path.is_file():
            raise SystemExit(f"{path} is missing: this run has no answer for {slug}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        rebuilt = build_summary(payload)
        if rebuilt != payload.get("summary"):
            raise SystemExit(f"{slug}: the run's summary is not what the code builds from its rows")
        text = render(payload)
        reduced = reduce(payload)
        if build_summary(reduced) != payload["summary"] or render(reduced) != text:
            raise SystemExit(f"{slug}: the reduced input does not render as the whole answer")
        made[slug] = (text, encode(reduced))
    return made


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="a run.py output directory")
    parser.add_argument("--write", action="store_true", help="replace the goldens instead of checking them")
    args = parser.parse_args(argv)
    made = build(args.out.expanduser())
    drift = []
    for slug, (text, encoded) in made.items():
        text_path, input_path = GOLDENS / f"{slug}.txt", GOLDENS / f"{slug}.input.json.gz"
        if args.write:
            GOLDENS.mkdir(exist_ok=True)
            text_path.write_text(text, encoding="utf-8")
            input_path.write_bytes(encoded)
            continue
        if not text_path.is_file() or text_path.read_text(encoding="utf-8") != text:
            drift.append(text_path.name)
        if not input_path.is_file() or gzip.decompress(input_path.read_bytes()) != gzip.decompress(encoded):
            drift.append(input_path.name)
    for name in drift:
        print(f"differs: {name}", file=sys.stderr)
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
