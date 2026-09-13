"""Freeze a #660 cold-start population: 30 public repositories, 10 per supported file.

Selection rule, recorded with the output so a rerun cannot silently pick an
easier or newer population:

* GitHub code search, one query per supported root file, results in the order
  the API returned them (recorded verbatim);
* exact root path only (no `.bak`, no nested copies);
* public, not a fork, not archived;
* >= 2 commits touching that file on the default branch in the 90 days before
  the selection date;
* head = the newest such non-merge commit, base = its only parent.

Every candidate considered is written out, accepted or rejected with its reason.
"""
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(sys.argv[1])
PER_KIND = 10
KINDS = {
    ".claude/settings.json": "filename:settings.json path:.claude",
    ".mcp.json": "filename:.mcp.json",
    ".cursor/mcp.json": "filename:mcp.json path:.cursor",
}
now = dt.datetime.now(dt.UTC).replace(microsecond=0)
since = (now - dt.timedelta(days=90)).isoformat().replace("+00:00", "Z")


def gh(endpoint, **params):
    args = ["gh", "api", "-X", "GET", endpoint]
    for key, value in params.items():
        args += ["-f", f"{key}={value}"]
    for _attempt in range(4):
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout)
        if "rate limit" in (r.stderr + r.stdout).lower():
            time.sleep(65)
            continue
        return {"_error": (r.stderr or r.stdout).strip()[:300]}
    return {"_error": "rate limited"}


record = {"selection_date": now.isoformat(), "window_since": since, "rule": __doc__, "queries": KINDS, "search_order": {}, "accepted": [], "rejected": []}
seen = set()
for path, query in KINDS.items():
    accepted = 0
    order = []
    for page in range(1, 4):
        if accepted >= PER_KIND:
            break
        result = gh("search/code", q=query, per_page=100, page=page)
        time.sleep(7)
        if "_error" in result:
            record["rejected"].append({"kind": path, "query_page": page, "reason": "search_error:" + result["_error"]})
            break
        for item in result.get("items", []):
            repo = item["repository"]["full_name"]
            order.append(f"{repo} {item['path']}")
            if accepted >= PER_KIND:
                continue
            if item["path"] != path:
                continue  # not the exact root file; recorded in search_order only
            if repo in seen:
                record["rejected"].append({"repo": repo, "kind": path, "reason": "already_selected_for_another_file"})
                continue
            seen.add(repo)
            meta = gh(f"repos/{repo}")
            if "_error" in meta:
                record["rejected"].append({"repo": repo, "kind": path, "reason": "repo_unreadable"})
                continue
            if meta.get("private") or meta.get("fork") or meta.get("archived"):
                record["rejected"].append({"repo": repo, "kind": path, "reason": "fork_archived_or_private"})
                continue
            commits = gh(f"repos/{repo}/commits", path=path, since=since, per_page=20, sha=meta["default_branch"])
            if not isinstance(commits, list):
                record["rejected"].append({"repo": repo, "kind": path, "reason": "commits_unreadable"})
                continue
            if len(commits) < 2:
                record["rejected"].append({"repo": repo, "kind": path, "reason": f"commits_in_window={len(commits)}"})
                continue
            head = next((c for c in commits if len(c.get("parents", [])) == 1), None)
            if head is None:
                record["rejected"].append({"repo": repo, "kind": path, "reason": "no_non_merge_commit"})
                continue
            record["accepted"].append({
                "repo": repo, "kind": path, "default_branch": meta["default_branch"],
                "head_sha": head["sha"], "base_sha": head["parents"][0]["sha"],
                "head_committed_at": head["commit"]["committer"]["date"],
                "commits_in_window": len(commits), "stars": meta.get("stargazers_count"),
            })
            accepted += 1
            print(f"ACCEPT {path:24} {repo} ({len(commits)} commits)", flush=True)
    record["search_order"][path] = order
    print(f"{path}: accepted {accepted}", flush=True)
(OUT / "selection.json").write_text(json.dumps(record, indent=2) + "\n")
print(f"accepted={len(record['accepted'])} rejected={len(record['rejected'])}")
