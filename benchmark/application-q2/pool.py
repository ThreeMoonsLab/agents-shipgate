"""Record the holdout's repository pool from GitHub code search (#908).

Usage:
    python benchmark/application-q2/pool.py --out benchmark/application-q2/pool.json

Code search returns at most 1,000 results per query, ranked, so the pool is a
recorded sample, not the population: each query is split into file-size
shards, each shard is read up to that cap, and every request, its total count
and the repositories it returned are written down. The pool was taken once,
before any of the reader changes the holdout judges merged; re-running this
script later does not reproduce it and is not how the holdout is extended.
Uses the ``gh`` CLI's authentication; code search allows 10 requests a minute.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

QUERIES = (
    '"from agents import" "Agent(" language:Python',
    '"from google.adk" "Agent(" language:Python',
)
SIZE_SHARDS = (
    "size:<1000",
    "size:1000..2000",
    "size:2000..3000",
    "size:3000..4500",
    "size:4500..6500",
    "size:6500..9000",
    "size:9000..13000",
    "size:>13000",
)
PAGES = 10  # 10 x 100 = the 1,000-result cap
REQUEST_INTERVAL_SECONDS = 6.5  # under the 10 requests/minute code-search limit


def _search(query: str, page: int) -> dict:
    for attempt in range(5):
        done = subprocess.run(
            ["gh", "api", "-X", "GET", "search/code", "-f", f"q={query}",
             "-f", "per_page=100", "-f", f"page={page}"],
            capture_output=True, text=True, timeout=120,
        )
        if done.returncode == 0:
            return json.loads(done.stdout)
        if "Cannot access beyond the first 1000 results" in done.stderr:
            return {"items": [], "beyond_cap": True}
        time.sleep(30 * (attempt + 1))
    raise SystemExit(f"code search kept failing for {query!r} page {page}: {done.stderr[-300:]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    requests: list[dict] = []
    repositories: set[str] = set()
    for query in QUERIES:
        for shard in SIZE_SHARDS:
            full = f"{query} {shard}"
            total = None
            for page in range(1, PAGES + 1):
                answer = _search(full, page)
                time.sleep(REQUEST_INTERVAL_SECONDS)
                items = answer.get("items", [])
                if total is None:
                    total = answer.get("total_count")
                found = sorted({
                    item["repository"]["full_name"]
                    for item in items
                    if not item["repository"].get("fork")
                })
                repositories.update(found)
                requests.append({"query": full, "page": page, "total_count": total,
                                 "items": len(items), "repositories": found})
                if len(items) < 100:
                    break
    document = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "queries": list(QUERIES),
        "size_shards": list(SIZE_SHARDS),
        "requests": requests,
        "repositories": sorted(repositories),
    }
    args.out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    print(f"{len(repositories)} repositories from {len(requests)} requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
