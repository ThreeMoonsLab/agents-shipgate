"""Freeze a #659 host-config PR population: 50 merged public PRs, fixed quotas per file kind.

Selection rule, recorded with the output:

* GitHub code search per file kind; candidates in the order the API returned them;
* exact root path (`.github/workflows/*.yml|yaml` for the workflow kind);
* public, not a fork, not archived; one PR per repository;
* the newest commit touching that path in the 365 days before selection that
  belongs to a merged PR; base = merge_commit^1 and head = merge_commit, the
  convention `benchmark/miner` uses;
* workflow kind only: the PR's patch for that file must add or remove a
  `permissions` line or a scope level (`read`/`write`/`none`, `read-all`/`write-all`).

Coding-agent co-authorship is recorded as metadata, never as a selection or risk signal.
"""
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(sys.argv[1])
KINDS = [
    (".claude/settings.json", "filename:settings.json path:.claude", 10),
    (".mcp.json", "filename:.mcp.json", 10),
    (".cursor/mcp.json", "filename:mcp.json path:.cursor", 7),
    (".codex/config.toml", "filename:config.toml path:.codex", 7),
    (".vscode/mcp.json", "filename:mcp.json path:.vscode", 6),
    (".github/workflows/", "path:.github/workflows permissions pull-requests write", 10),
]
now = dt.datetime.now(dt.UTC).replace(microsecond=0)
since = (now - dt.timedelta(days=365)).isoformat().replace("+00:00", "Z")
PERMISSION_LINE = re.compile(r"^[+-]\s*(permissions\s*:|[a-z-]+\s*:\s*(read|write|none)\s*$|(read|write)-all\b)", re.M)
AGENT_MARK = re.compile(r"(?i)co-authored-by:\s*(claude|codex|cursor|copilot|devin)|generated with \[?claude code|cursor agent", re.M)


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
        return {"_error": (r.stderr or r.stdout).strip()[:200]}
    return {"_error": "rate limited"}


def path_matches(kind, path):
    if kind.endswith("/"):
        return path.startswith(kind) and path.count("/") == 2 and path.endswith((".yml", ".yaml"))
    return path == kind


record = {"selection_date": now.isoformat(), "window_since": since, "rule": __doc__, "queries": {k: q for k, q, _ in KINDS}, "search_order": {}, "accepted": [], "rejected": []}
seen = set()
for kind, query, quota in KINDS:
    accepted, order = 0, []
    for page in range(1, 6):
        if accepted >= quota:
            break
        result = gh("search/code", q=query, per_page=100, page=page)
        time.sleep(7)
        if "_error" in result:
            record["rejected"].append({"kind": kind, "query_page": page, "reason": "search_error:" + result["_error"]})
            break
        for item in result.get("items", []):
            repo, path = item["repository"]["full_name"], item["path"]
            order.append(f"{repo} {path}")
            if accepted >= quota or not path_matches(kind, path):
                continue
            if repo in seen:
                continue
            seen.add(repo)
            meta = gh(f"repos/{repo}")
            if "_error" in meta or meta.get("private") or meta.get("fork") or meta.get("archived"):
                record["rejected"].append({"repo": repo, "kind": kind, "reason": "unreadable_fork_archived_or_private"})
                continue
            commits = gh(f"repos/{repo}/commits", path=path, since=since, per_page=15, sha=meta["default_branch"])
            if not isinstance(commits, list) or not commits:
                record["rejected"].append({"repo": repo, "kind": kind, "reason": "no_commits_in_window"})
                continue
            chosen = None
            for commit in commits:
                pulls = gh(f"repos/{repo}/commits/{commit['sha']}/pulls")
                merged = [p for p in pulls if isinstance(p, dict) and p.get("merged_at") and p.get("merge_commit_sha")] if isinstance(pulls, list) else []
                if not merged:
                    continue
                pr = merged[0]
                if kind.endswith("/"):
                    files = gh(f"repos/{repo}/pulls/{pr['number']}/files", per_page=100)
                    patch = next((f.get("patch") or "" for f in files if isinstance(f, dict) and f.get("filename") == path), "") if isinstance(files, list) else ""
                    if not PERMISSION_LINE.search(patch):
                        continue
                chosen = (commit, pr)
                break
            if chosen is None:
                record["rejected"].append({"repo": repo, "kind": kind, "reason": "no_merged_pr" + ("_changing_permissions" if kind.endswith("/") else "")})
                continue
            commit, pr = chosen
            record["accepted"].append({
                "repo": repo, "kind": kind, "path": path, "pr_number": pr["number"], "pr_title": pr.get("title"),
                "merged_at": pr["merged_at"], "merge_commit_sha": pr["merge_commit_sha"], "touching_commit_sha": commit["sha"],
                "coding_agent_marked": bool(AGENT_MARK.search(commit["commit"].get("message") or "")),
                "default_branch": meta["default_branch"],
            })
            accepted += 1
            print(f"ACCEPT {kind:24} {repo} #{pr['number']}", flush=True)
    record["search_order"][kind] = order
    print(f"{kind}: accepted {accepted}/{quota}", flush=True)
(OUT / "selection.json").write_text(json.dumps(record, indent=2) + "\n")
print(f"accepted={len(record['accepted'])} rejected={len(record['rejected'])}")
