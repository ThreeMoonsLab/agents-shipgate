#!/usr/bin/env python
"""Run the experimental AgentPR governance benchmark.

This runner is intentionally internal: it emits a deterministic eval artifact
for product development, not a stable user-facing CLI surface.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from agents_shipgate.core.errors import ConfigError, InputParseError  # noqa: E402
from agents_shipgate.core.governance_benchmark import (  # noqa: E402
    GovernanceBenchmarkOptions,
    benchmark_exit_code,
    load_governance_benchmark_catalog,
    render_governance_benchmark_result_json,
    run_governance_benchmark,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_governance_benchmark",
        description="Run the experimental AgentPR governance benchmark.",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("benchmark/agent-pr-governance/cases.yaml"),
        help="Governance benchmark catalog YAML.",
    )
    parser.add_argument(
        "--case",
        dest="cases",
        action="append",
        default=[],
        help="Run one case id. May be repeated.",
    )
    parser.add_argument(
        "--category",
        dest="categories",
        action="append",
        default=[],
        help="Run one category. May be repeated.",
    )
    parser.add_argument(
        "--include-catalog-only",
        action="store_true",
        help="Include catalog-only and external-evidence rows as skipped results.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat included non-executable rows as failures.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write result JSON to this path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print result JSON to stdout.",
    )
    args = parser.parse_args(argv)

    try:
        catalog = load_governance_benchmark_catalog(args.catalog)
        result = run_governance_benchmark(
            catalog,
            catalog_path=args.catalog,
            options=GovernanceBenchmarkOptions(
                case_ids=frozenset(args.cases),
                categories=frozenset(args.categories),
                include_catalog_only=args.include_catalog_only,
                strict=args.strict,
            ),
        )
        rendered = render_governance_benchmark_result_json(result)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(rendered, encoding="utf-8")
        if args.json or args.out is None:
            sys.stdout.write(rendered)
        else:
            sys.stdout.write(f"Wrote governance benchmark result to {args.out}\n")
        return benchmark_exit_code(result)
    except ConfigError as exc:
        sys.stderr.write(f"Config error: {exc}\n")
        return 2
    except InputParseError as exc:
        sys.stderr.write(f"Input parsing error: {exc}\n")
        return 3
    except Exception as exc:  # noqa: BLE001 - script boundary.
        sys.stderr.write(f"Internal error: {exc}\n")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
