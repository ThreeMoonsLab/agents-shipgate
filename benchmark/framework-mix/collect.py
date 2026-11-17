"""How the framework-mix sample was recorded (#916).

Usage (each step reads what the one before it wrote under ``--work``):

    python benchmark/framework-mix/collect.py --work <dir> frame-a --slices 24
    python benchmark/framework-mix/collect.py --work <dir> candidates
    python benchmark/framework-mix/collect.py --work <dir> fetch --limit 250
    python benchmark/framework-mix/collect.py --work <dir> blobs --limit 250
    python benchmark/framework-mix/collect.py --work <dir> dossier --start 0 --limit 250
    python benchmark/framework-mix/collect.py --work <dir> frame-b
    python benchmark/framework-mix/collect.py --work <dir> b-walk --limit 200

Two frames, both read-only through the ``gh`` CLI's authentication:

* **neutral**: GitHub's pull-request search for titles that name an agent and
  a tool, a handoff or a sub-agent, in hours drawn at random from the window.
  Nothing in the query names a framework, so the framework of each relevant
  pull request is a finding, not a selection.
* **framework:<name>**: code search for a framework's import and tool-binding
  idiom, the 15 most recent pull requests of each repository it returns, and
  a prefilter for a changed line that matches the framework's binding idiom
  in a file that imports the framework. It finds that framework's pull
  requests to judge their readability; it says nothing about share.

Search results drift, so re-running this does not reproduce ``sample.json``;
the pins, the requests and their counts are what the sample records. The
``dossier`` and ``b-walk`` steps write what the judge read; the judgments
themselves were made by hand from source and are recorded in ``sample.json``.
It never runs a repository's code, never runs Agents Shipgate, and skips every
repository of the application holdout's recorded pool before reading it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

WORK = Path.cwd() / "framework-mix-work"  # replaced by --work
REPO = Path(__file__).resolve().parents[2]
WINDOW_START = datetime(2026, 8, 19, tzinfo=UTC)
WINDOW_END = datetime(2026, 11, 17, tzinfo=UTC)  # exclusive
SEED = "issue-916-framework-mix"
TITLE_QUERIES = (
    "agent tool in:title",
    "agent handoff in:title",
    "subagent in:title",
    '"sub-agent" in:title',
)

PR_FIELDS = """
... on PullRequest {
  url number title createdAt state merged isDraft headRefOid baseRefOid changedFiles additions deletions
  repository { nameWithOwner isFork isArchived primaryLanguage { name } }
  files(first: 100) { totalCount nodes { path additions deletions changeType } }
}
"""


def gh_graphql(query: str, **variables: str | int | None) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None:
            continue
        flag = "-F" if isinstance(value, int) else "-f"
        args += [flag, f"{key}={value}"]
    for attempt in range(6):
        done = subprocess.run(args, capture_output=True, text=True, timeout=180)
        if done.returncode == 0:
            payload = json.loads(done.stdout)
            if payload.get("errors") and not payload.get("data"):
                raise SystemExit(f"graphql errors: {payload['errors']}")
            return payload
        time.sleep(20 * (attempt + 1))
    raise SystemExit(f"graphql kept failing: {done.stderr[-400:]}")


def gh_rest(path: str, accept: str | None = None) -> dict | list | None:
    args = ["gh", "api", path]
    if accept:
        args += ["-H", f"Accept: {accept}"]
    for attempt in range(5):
        done = subprocess.run(args, capture_output=True, text=True, timeout=180)
        if done.returncode == 0:
            return json.loads(done.stdout)
        if "HTTP 404" in done.stderr or "HTTP 422" in done.stderr or "HTTP 451" in done.stderr:
            return None
        time.sleep(15 * (attempt + 1))
    print(f"rest failed {path}: {done.stderr[-300:]}", file=sys.stderr)
    return None


def hours() -> list[datetime]:
    out, t = [], WINDOW_START
    while t < WINDOW_END:
        out.append(t)
        t += timedelta(hours=1)
    return out


def hour_order() -> list[str]:
    stamps = [h.strftime("%Y-%m-%dT%H:00:00Z") for h in hours()]
    return sorted(stamps, key=lambda s: hashlib.sha256(f"{SEED}:{s}".encode()).hexdigest())


def search_slice(query: str, start: str) -> dict:
    begin = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    end = begin + timedelta(minutes=59, seconds=59)
    full = f"is:pr is:public created:{start}..{end.strftime('%Y-%m-%dT%H:%M:%SZ')} {query}"
    gql = "query($q:String!,$after:String){search(query:$q,type:ISSUE,first:100,after:$after){issueCount pageInfo{hasNextPage endCursor} nodes{" + PR_FIELDS + "}}}"
    nodes, after, total = [], None, None
    while True:
        payload = gh_graphql(gql, q=full, after=after)
        search = payload["data"]["search"]
        total = search["issueCount"]
        nodes += [n for n in search["nodes"] if n]
        if not search["pageInfo"]["hasNextPage"]:
            break
        after = search["pageInfo"]["endCursor"]
    return {"query": full, "issue_count": total, "returned": len(nodes), "nodes": nodes,
            "run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


def cmd_frame_a(args: argparse.Namespace) -> None:
    out = WORK / "frame_a"
    out.mkdir(parents=True, exist_ok=True)
    order = hour_order()
    for index, start in enumerate(order[args.start: args.start + args.slices], args.start):
        target = out / f"slice_{index:04d}.json"
        if target.exists():
            continue
        results = [search_slice(q, start) for q in TITLE_QUERIES]
        target.write_text(json.dumps({"index": index, "hour": start, "results": results}, indent=1))
        print(index, start, [r["issue_count"] for r in results], flush=True)
        time.sleep(1)


# ---------------------------------------------------------------- candidates
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".go", ".cs",
            ".java", ".kt", ".rs", ".rb", ".php", ".swift", ".scala", ".ex", ".exs"}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e", "testdata", "fixtures", "__mocks__"}


def is_code(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXT


def is_test(path: str) -> bool:
    parts = Path(path).parts
    if any(part.lower() in TEST_DIRS for part in parts[:-1]):
        return True
    name = Path(path).name.lower()
    return (name.startswith("test_") or name.startswith("test.") or name == "conftest.py"
            or re.search(r"(_test\.(py|go)|\.(test|spec)\.[cm]?[jt]sx?|tests?\.cs|test\.java|test\.kt)$", name) is not None)


def pool_repos() -> set[str]:
    pool = json.loads((REPO / "benchmark/application-q2/pool.json").read_text())
    return {r.lower() for r in pool["repositories"]}


def development_urls() -> set[str]:
    dev = json.loads((REPO / "benchmark/application-q2/development.json").read_text())
    return {m["url"].lower() for m in dev["members"]}


def frame_a_nodes() -> tuple[list[dict], list[dict]]:
    """Every PR the walked slices returned, deduplicated, with each slice's log."""
    seen: dict[str, dict] = {}
    log = []
    for path in sorted((WORK / "frame_a").glob("slice_*.json")):
        data = json.loads(path.read_text())
        for result in data["results"]:
            log.append({"slice": data["index"], "hour": data["hour"], "query": result["query"],
                        "issue_count": result["issue_count"], "returned": result["returned"],
                        "run_at": result["run_at"]})
            for node in result["nodes"]:
                if "url" not in node:
                    continue
                key = node["url"].lower()
                if key not in seen:
                    node = dict(node)
                    node["frame"] = "neutral-titles"
                    node["slice"] = data["index"]
                    seen[key] = node
    return list(seen.values()), log


def frame_b_nodes() -> tuple[list[dict], list[dict]]:
    seen: dict[str, dict] = {}
    log = []
    for path in sorted((WORK / "frame_b").glob("*.json")):
        data = json.loads(path.read_text())
        log += data.get("log", [])
        for node in data.get("nodes", []):
            key = node["url"].lower()
            if key not in seen:
                seen[key] = node
    return list(seen.values()), log


def exclusion(node: dict, pool: set[str], dev: set[str]) -> str | None:
    repo = node["repository"]["nameWithOwner"].lower()
    if repo in pool:
        return "holdout_pool_repository"
    if node["url"].lower() in dev:
        return "development_corpus_member"
    if node["state"] == "CLOSED" and not node["merged"]:
        return "closed_unmerged"
    files = [f["path"] for f in (node.get("files") or {}).get("nodes", [])]
    if (node.get("files") or {}).get("totalCount", 0) > len(files):
        return None  # more than 100 files: the compare step reads the full list
    if not any(is_code(p) and not is_test(p) for p in files):
        return "no_non_test_code_file"
    return None


def cmd_candidates(args: argparse.Namespace) -> None:
    nodes, log = frame_a_nodes() if args.frame == "a" else frame_b_nodes()
    pool, dev = pool_repos(), development_urls()
    counts: dict[str, int] = {}
    keep = []
    for node in nodes:
        reason = exclusion(node, pool, dev)
        counts[reason or "kept"] = counts.get(reason or "kept", 0) + 1
        if reason is None:
            keep.append(node)
    keep.sort(key=lambda n: hashlib.sha256(f"{SEED}:{n['url'].lower()}".encode()).hexdigest())
    out = WORK / f"candidates_{args.frame}.json"
    out.write_text(json.dumps({"counts": counts, "log": log, "candidates": keep}, indent=1))
    print(json.dumps(counts, indent=1), len(nodes))


# ---------------------------------------------------------------- fetch
def owner_repo(node: dict) -> tuple[str, str]:
    owner, name = node["repository"]["nameWithOwner"].split("/")
    return owner, name


def key_of(node: dict) -> str:
    owner, name = owner_repo(node)
    return f"{owner}__{name}__{node['number']}"


def cmd_fetch(args: argparse.Namespace) -> None:
    cands = json.loads((WORK / f"candidates_{args.frame}.json").read_text())["candidates"]
    out = WORK / "compare"
    out.mkdir(exist_ok=True)
    for node in cands[: args.limit]:
        owner, name = owner_repo(node)
        target = out / f"{key_of(node)}.json"
        if target.exists():
            continue
        data = gh_rest(f"repos/{owner}/{name}/compare/{node['baseRefOid']}...{node['headRefOid']}")
        if data is None:
            target.write_text(json.dumps({"error": "compare unavailable"}))
            continue
        files = [{k: f.get(k) for k in ("filename", "status", "additions", "deletions", "patch", "previous_filename")}
                 for f in data.get("files", [])]
        target.write_text(json.dumps({
            "merge_base": data["merge_base_commit"]["sha"], "head": node["headRefOid"],
            "base_ref_oid": node["baseRefOid"], "files": files,
            "files_truncated": len(data.get("files", [])) >= 300,
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }))
        print("fetched", target.name, len(files), flush=True)


# ---------------------------------------------------------------- prefilter
IDIOM = re.compile(r"(?i)(tool|handoff|sub[_-]?agent|kernel_function|register_for_llm|register_function|add_plugin|function_map)")


def changed_lines(patch: str | None) -> list[tuple[str, int, str]]:
    """(sign, line number on its side, text) for every +/- line of a unified patch."""
    out = []
    if not patch:
        return out
    old = new = 0
    for line in patch.splitlines():
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", line)
            if m:
                old, new = int(m.group(1)), int(m.group(2))
            continue
        if line.startswith("+"):
            out.append(("+", new, line[1:]))
            new += 1
        elif line.startswith("-"):
            out.append(("-", old, line[1:]))
            old += 1
        else:
            old += 1
            new += 1
    return out


def prefilter_hits(compare: dict) -> list[str]:
    hits = []
    for f in compare.get("files", []):
        path = f["filename"]
        if not is_code(path) or is_test(path):
            continue
        if any(IDIOM.search(text) for _, _, text in changed_lines(f.get("patch"))):
            hits.append(path)
    return hits


# ---------------------------------------------------------------- imports / framework tags
FRAMEWORK_PATTERNS = (
    ("openai_agents_sdk", r"^\s*(from agents(\.[\w.]+)? import|import agents\b)"),
    ("openai_agents_js", r"@openai/agents"),
    ("google_adk", r"google\.adk|@google/adk|google/adk-go|google\.golang\.org/adk"),
    ("langchain_langgraph", r"\blanggraph\b|\blangchain|@langchain/"),
    ("crewai", r"\bcrewai"),
    ("pydantic_ai", r"\bpydantic_ai\b"),
    ("llamaindex", r"\bllama_index\b|llamaindex"),
    ("autogen_ag2", r"\bautogen(_agentchat|_core|_ext)?\b|\bag2\b"),
    ("smolagents", r"\bsmolagents\b"),
    ("agno", r"^\s*(from|import) (agno|phi)\b"),
    ("semantic_kernel", r"semantic_kernel|Microsoft\.SemanticKernel"),
    ("mcp_python", r"^\s*from (mcp(\.server[\w.]*)?|fastmcp) import|^\s*import fastmcp|FastMCP\("),
    ("mcp_typescript", r"@modelcontextprotocol/sdk|from ['\"]fastmcp['\"]|mcp-framework|McpServer\("),
    ("mcp_go", r"mark3labs/mcp-go|modelcontextprotocol/go-sdk|metoro-io/mcp-golang"),
    ("vercel_ai_sdk", r"from ['\"]ai['\"]|require\(['\"]ai['\"]\)|@ai-sdk/"),
    ("mastra", r"@mastra/"),
    ("claude_agent_sdk", r"claude_agent_sdk|@anthropic-ai/claude-agent-sdk|claude-code-sdk|claude_code_sdk"),
    ("microsoft_agent_framework", r"^\s*(from|import) agent_framework\b|Microsoft\.Agents\.AI"),
    ("strands", r"^\s*(from|import) strands\b"),
    ("livekit_agents", r"livekit\.agents|@livekit/agents"),
    ("dspy", r"^\s*(from|import) dspy\b"),
    ("haystack", r"^\s*(from|import) haystack\b"),
    ("openai_raw", r"^\s*(from openai import|import openai\b)|from ['\"]openai['\"]"),
    ("anthropic_raw", r"^\s*(from anthropic import|import anthropic\b)|@anthropic-ai/sdk"),
    ("google_genai_raw", r"google\.genai|google\.generativeai|@google/genai"),
    ("litellm", r"\blitellm\b"),
)
IMPORT_LINE = re.compile(r"^\s*(from\s+[\w.]+\s+import|import\s|.*\brequire\(|.*\bfrom\s+['\"]|using\s+[\w.]+;|\"[\w./-]+\"$|package\s)")


def import_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines()[:400]:
        if IMPORT_LINE.match(line) and len(line) < 200:
            out.append(line.rstrip())
    return out


def framework_tags(lines: list[str]) -> list[str]:
    tags = []
    for name, pattern in FRAMEWORK_PATTERNS:
        rx = re.compile(pattern, re.M)
        if any(rx.search(line) for line in lines):
            tags.append(name)
    return tags


def fetch_blobs(owner: str, name: str, requests: list[tuple[str, str]]) -> dict[tuple[str, str], str | None]:
    """(sha, path) -> text via one GraphQL query per 20 objects."""
    out: dict[tuple[str, str], str | None] = {}
    for start in range(0, len(requests), 20):
        chunk = requests[start: start + 20]
        parts = []
        for i, (sha, path) in enumerate(chunk):
            expr = json.dumps(f"{sha}:{path}")
            parts.append(f"f{i}: object(expression: {expr}) {{ ... on Blob {{ text isBinary byteSize }} }}")
        query = f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {' '.join(parts)} }} }}"
        try:
            payload = gh_graphql(query)
        except SystemExit:
            for item in chunk:
                out[item] = None
            continue
        repo = (payload.get("data") or {}).get("repository") or {}
        for i, item in enumerate(chunk):
            obj = repo.get(f"f{i}")
            out[item] = obj.get("text") if obj and not obj.get("isBinary") else None
    return out


def cmd_blobs(args: argparse.Namespace) -> None:
    """Fetch the touched non-test code files that the prefilter flagged, at head (or base if removed)."""
    cands = json.loads((WORK / f"candidates_{args.frame}.json").read_text())["candidates"]
    out = WORK / "blobs"
    out.mkdir(exist_ok=True)
    done = 0
    for node in cands[: args.limit]:
        key = key_of(node)
        cpath = WORK / "compare" / f"{key}.json"
        target = out / f"{key}.json"
        if target.exists() or not cpath.exists():
            continue
        compare = json.loads(cpath.read_text())
        if "error" in compare:
            continue
        hits = prefilter_hits(compare)
        if not hits:
            target.write_text(json.dumps({"prefilter": [], "files": {}}))
            continue
        status = {f["filename"]: f["status"] for f in compare["files"]}
        code = [f["filename"] for f in compare["files"] if is_code(f["filename"]) and not is_test(f["filename"])]
        wanted = (hits + [p for p in code if p not in hits])[:12]
        requests = [((compare["merge_base"] if status.get(p) == "removed" else compare["head"]), p) for p in wanted]
        owner, name = owner_repo(node)
        texts = fetch_blobs(owner, name, requests)
        files = {p: texts.get(req) for p, req in zip(wanted, requests, strict=True)}
        target.write_text(json.dumps({"prefilter": hits, "files": files}))
        done += 1
        print("blobs", key, len(files), flush=True)
    print("done", done)


STRONG = re.compile(
    r"tools\s*[=:]\s*[\[\({]|tools=|\btools\s*=|\.tool\(|@tool\b|@function_tool|function_tool\(|@\w+\.tool\b|"
    r"\.tool\s*\(|registerTool|register_tool|add_tool|addTool|add_tools|sub_agents|subAgents|handoffs?\b|"
    r"\bTool\(|FunctionTool|StructuredTool|tool\(\{|createTool|defineTool|kernel_function|add_plugin|"
    r"register_for_llm|AgentTool|as_tool|bind_tools|ToolNode|create_react_agent|create_agent|\bAgent\(|"
    r"\bLlmAgent\(|\bCrew\(|\bTOOLS\b|_TOOLS\b|tool_registry|ToolRegistry|\.register\(|toolsets?\s*=|"
    r"mcp_servers|McpToolset|MCPServer|tool_names|toolNames|allowed_tools|function_declarations"
)


def dossier(node: dict, index: int) -> str:
    key = key_of(node)
    compare = json.loads((WORK / "compare" / f"{key}.json").read_text())
    blobs_path = WORK / "blobs" / f"{key}.json"
    blobs = json.loads(blobs_path.read_text()) if blobs_path.exists() else {"prefilter": [], "files": {}}
    state = "merged" if node["merged"] else node["state"].lower()
    lines = [f"### [{index}] {node['url']}  ({state}{', draft' if node.get('isDraft') else ''}"
             f"{', fork' if node['repository']['isFork'] else ''}) {node['createdAt'][:10]}",
             f"title: {node['title'][:160]}"]
    if "error" in compare:
        lines.append("compare unavailable")
        return "\n".join(lines)
    files = compare["files"]
    code = [f for f in files if is_code(f["filename"]) and not is_test(f["filename"])]
    tests = [f for f in files if is_code(f["filename"]) and is_test(f["filename"])]
    other = len(files) - len(code) - len(tests)
    lines.append(f"files: {len(files)}{'+' if compare.get('files_truncated') else ''} | code: "
                 + ", ".join(f"{f['filename']}({f['status'][0]} +{f['additions']}/-{f['deletions']})" for f in code[:5])
                 + (f" ...(+{len(code) - 5})" if len(code) > 5 else "")
                 + f" | tests: {len(tests)} | other: {other}")
    all_imports: list[str] = []
    shown_imports = 0
    for path, text in blobs["files"].items():
        if not text:
            continue
        imps = import_lines(text)
        all_imports += imps
        keep = [i.strip() for i in imps if framework_tags([i])]
        if keep and shown_imports < 4:
            lines.append(f"  imports {path}: " + " ; ".join(keep[:3])[:240])
            shown_imports += 1
    lines.append(f"tags: {framework_tags(all_imports)}")
    shown = 0
    for f in code:
        if shown >= 16:
            break
        changed = changed_lines(f.get("patch"))
        strong = [c for c in changed if STRONG.search(c[2])]
        weak = [c for c in changed if IDIOM.search(c[2]) and c not in strong]
        pick = strong[:7] if strong else weak[:3]
        if not pick:
            continue
        lines.append(f"  -- {f['filename']}{' [no patch]' if not f.get('patch') else ''}")
        for sign, num, text in pick:
            lines.append(f"   {sign}{num}: {text.strip()[:130]}")
            shown += 1
    return "\n".join(lines)


def cmd_dossier(args: argparse.Namespace) -> None:
    cands = json.loads((WORK / f"candidates_{args.frame}.json").read_text())["candidates"]
    chunks = []
    for index, node in enumerate(cands[args.start: args.start + args.limit], args.start):
        blobs_path = WORK / "blobs" / f"{key_of(node)}.json"
        if not blobs_path.exists():
            continue
        if not json.loads(blobs_path.read_text())["prefilter"]:
            continue
        chunks.append(dossier(node, index))
    out = WORK / "dossiers" / f"{args.frame}_{args.start:04d}.txt"
    out.write_text("\n\n".join(chunks) + "\n")
    print(out, len(chunks))


# ---------------------------------------------------------------- frame B: per-framework code search
CODE_QUERIES = {
    "openai_agents_sdk": ['"from agents import" "function_tool" "tools=" language:Python'],
    "google_adk": ['"from google.adk" "tools=" language:Python'],
    "langchain_langgraph": ['"create_react_agent" "tools" language:Python', '"from langchain" "create_agent" "tools=" language:Python'],
    "crewai": ['"from crewai import" "tools=" language:Python'],
    "pydantic_ai": ['"from pydantic_ai import" "tools=" language:Python', '"from pydantic_ai" "@agent.tool" language:Python'],
    "llamaindex": ['"from llama_index" "FunctionTool" language:Python'],
    "autogen_ag2": ['"from autogen_agentchat" "tools=" language:Python', '"from autogen import" "register_function" language:Python'],
    "smolagents": ['"from smolagents import" "tools=" language:Python'],
    "agno": ['"from agno.agent import Agent" "tools=" language:Python'],
    "semantic_kernel": ['"semantic_kernel" "kernel_function" language:Python', '"Microsoft.SemanticKernel" "KernelFunction" language:C#'],
    "mcp_server_python": ['"FastMCP" "@mcp.tool" language:Python'],
    "mcp_server_typescript": ['"@modelcontextprotocol/sdk/server" "registerTool" language:TypeScript', '"@modelcontextprotocol/sdk/server" "server.tool(" language:TypeScript'],
}
CODE_PAGES = 3


def code_search(query: str, page: int) -> dict:
    for attempt in range(6):
        done = subprocess.run(
            ["gh", "api", "-X", "GET", "search/code", "-f", f"q={query}", "-f", "per_page=100", "-f", f"page={page}"],
            capture_output=True, text=True, timeout=120)
        if done.returncode == 0:
            return json.loads(done.stdout)
        if "Cannot access beyond the first 1000 results" in done.stderr or "HTTP 422" in done.stderr:
            return {"items": [], "total_count": None}
        time.sleep(30 * (attempt + 1))
    raise SystemExit(f"code search failed {query!r}: {done.stderr[-300:]}")


def repo_prs(repos: list[str]) -> list[dict]:
    """The 15 most recently created PRs of each repository, with their file lists."""
    out = []
    for start in range(0, len(repos), 20):
        chunk = repos[start: start + 20]
        parts = []
        for i, full in enumerate(chunk):
            owner, name = full.split("/", 1)
            parts.append(f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) "
                         "{ pullRequests(first: 15, orderBy: {field: CREATED_AT, direction: DESC}) { nodes {"
                         + PR_FIELDS + "} } }")
        try:
            payload = gh_graphql("query { " + " ".join(parts) + " }")
        except SystemExit as error:
            print("graphql chunk failed", error, file=sys.stderr)
            continue
        data = payload.get("data") or {}
        for i in range(len(chunk)):
            repo = data.get(f"r{i}")
            if repo:
                out += [n for n in repo["pullRequests"]["nodes"] if n]
        time.sleep(1)
    return out


def cmd_frame_b(args: argparse.Namespace) -> None:
    out = WORK / "frame_b"
    out.mkdir(parents=True, exist_ok=True)
    pool = pool_repos()
    for framework, queries in CODE_QUERIES.items():
        if args.only and framework not in args.only.split(","):
            continue
        target = out / f"{framework}.json"
        if target.exists():
            continue
        log, repos = [], []
        for query in queries:
            for page in range(1, CODE_PAGES + 1):
                answer = code_search(query, page)
                time.sleep(6.5)
                items = answer.get("items", [])
                found = sorted({i["repository"]["full_name"] for i in items if not i["repository"].get("fork")})
                log.append({"framework": framework, "query": query, "page": page, "total_count": answer.get("total_count"),
                            "items": len(items), "repositories": len(found),
                            "run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")})
                repos += [r for r in found if r not in repos]
                if len(items) < 100:
                    break
        walk = [r for r in repos if r.lower() not in pool]
        prs = repo_prs(walk)
        start, end = WINDOW_START.strftime("%Y-%m-%dT%H:%M:%SZ"), WINDOW_END.strftime("%Y-%m-%dT%H:%M:%SZ")
        nodes = []
        for node in prs:
            if not (start <= node["createdAt"] < end):
                continue
            node = dict(node)
            node["frame"] = f"framework:{framework}"
            nodes.append(node)
        target.write_text(json.dumps({"framework": framework, "log": log, "repositories": len(repos),
                                      "pool_repositories_skipped": len(repos) - len(walk), "nodes": nodes}, indent=1))
        print(framework, "repos", len(repos), "walked", len(walk), "prs in window", len(nodes), flush=True)


# ---------------------------------------------------------------- frame B: per-framework walk
BINDING = {
    "openai_agents_sdk": r"tools\s*=|handoffs\s*=|@function_tool|function_tool\(|\.as_tool\(|mcp_servers\s*=|\bAgent\(",
    "google_adk": r"tools\s*=|sub_agents\s*=|AgentTool\(|FunctionTool\(|McpToolset|MCPToolset|LlmAgent\(|\bAgent\(",
    "langchain_langgraph": r"tools\s*=|bind_tools|create_react_agent|create_agent|ToolNode|@tool\b|StructuredTool|\bTool\(|tools\.(append|extend)|add_node",
    "crewai": r"tools\s*=|@tool\b|BaseTool|\bAgent\(|\bCrew\(|agents\s*=|tools\.(append|extend)",
    "pydantic_ai": r"tools\s*=|@\w+\.tool(_plain)?\b|toolsets\s*=|\bTool\(|FunctionToolset|\bAgent\(",
    "llamaindex": r"tools\s*=|FunctionTool|QueryEngineTool|ReActAgent|FunctionAgent|AgentWorkflow|can_handoff_to|from_tools",
    "autogen_ag2": r"tools\s*=|register_function|register_for_llm|register_for_execution|handoffs\s*=|FunctionTool|AssistantAgent\(|participants\s*=",
    "smolagents": r"tools\s*=|@tool\b|managed_agents\s*=|CodeAgent\(|ToolCallingAgent\(|\bTool\b",
    "agno": r"tools\s*=|@tool\b|Toolkit|\bAgent\(|\bTeam\(|members\s*=",
    "semantic_kernel": r"kernel_function|KernelFunction|add_plugin|AddPlugin|plugins\s*=|ImportPluginFrom|AddFromType|AddFromObject|functions\s*=",
    "mcp_server_python": r"@\w+\.tool\b|\.tool\(|add_tool|FastMCP|\bTool\(",
    "mcp_server_typescript": r"registerTool|\.tool\(|ListToolsRequestSchema|tools\s*:\s*\[|\bname\s*:\s*['\"]",
}
TAG_FOR = {
    "openai_agents_sdk": {"openai_agents_sdk"}, "google_adk": {"google_adk"},
    "langchain_langgraph": {"langchain_langgraph"}, "crewai": {"crewai"}, "pydantic_ai": {"pydantic_ai"},
    "llamaindex": {"llamaindex"}, "autogen_ag2": {"autogen_ag2"}, "smolagents": {"smolagents"}, "agno": {"agno"},
    "semantic_kernel": {"semantic_kernel"}, "mcp_server_python": {"mcp_python"},
    "mcp_server_typescript": {"mcp_typescript"},
}


FRAMEWORK_REPOSITORIES = {
    "openai/openai-agents-python", "google/adk-python", "google/adk-samples", "google/adk-go", "google/adk-java",
    "langchain-ai/langchain", "langchain-ai/langgraph", "langchain-ai/langchainjs", "langchain-ai/langgraphjs",
    "langchain-ai/deepagents", "crewaiinc/crewai", "crewaiinc/crewai-tools", "pydantic/pydantic-ai",
    "run-llama/llama_index", "microsoft/autogen", "ag2ai/ag2", "huggingface/smolagents", "agno-agi/agno",
    "microsoft/semantic-kernel", "modelcontextprotocol/python-sdk", "modelcontextprotocol/typescript-sdk",
    "modelcontextprotocol/servers", "jlowin/fastmcp", "prefecthq/fastmcp", "punkpeye/fastmcp",
}
REPO_CAP = 3  # flagged pull requests judged per repository and framework


def b_candidates() -> dict[str, list[dict]]:
    pool, dev = pool_repos(), development_urls()
    neutral = {n["url"].lower() for n in frame_a_nodes()[0]}
    out: dict[str, list[dict]] = {}
    counts: dict[str, dict[str, int]] = {}
    for path in sorted((WORK / "frame_b").glob("*.json")):
        data = json.loads(path.read_text())
        framework = data["framework"]
        seen, keep, c = set(), [], {}
        for node in data["nodes"]:
            key = node["url"].lower()
            if key in seen:
                continue
            seen.add(key)
            reason = exclusion(node, pool, dev)
            if reason is None and node["repository"]["nameWithOwner"].lower() in FRAMEWORK_REPOSITORIES:
                reason = "framework_repository"
            if reason is None and key in neutral:
                reason = "in_neutral_frame"
            c[reason or "kept"] = c.get(reason or "kept", 0) + 1
            if reason is None:
                keep.append(node)
        keep.sort(key=lambda n: hashlib.sha256(f"{SEED}:{framework}:{n['url'].lower()}".encode()).hexdigest())
        out[framework] = keep
        counts[framework] = c
    (WORK / "candidates_b.json").write_text(json.dumps({"counts": counts, "candidates": out}, indent=1))
    return out


def cmd_b_walk(args: argparse.Namespace) -> None:
    """Fetch, prefilter and write a dossier per framework for its first --limit candidates."""
    cands = b_candidates()
    for framework, nodes in cands.items():
        if args.only and framework not in args.only.split(","):
            continue
        rx = re.compile(BINDING[framework])
        chunks, flagged, per_repo, walked, index = [], 0, {}, 0, -1
        for index, node in enumerate(nodes):
            if walked >= args.limit:
                break
            repo = node["repository"]["nameWithOwner"].lower()
            if per_repo.get(repo, 0) >= REPO_CAP:
                continue
            walked += 1
            key = key_of(node)
            cpath = WORK / "compare" / f"{key}.json"
            if not cpath.exists():
                owner, name = owner_repo(node)
                data = gh_rest(f"repos/{owner}/{name}/compare/{node['baseRefOid']}...{node['headRefOid']}")
                if data is None:
                    cpath.write_text(json.dumps({"error": "compare unavailable"}))
                else:
                    files = [{k: f.get(k) for k in ("filename", "status", "additions", "deletions", "patch", "previous_filename")}
                             for f in data.get("files", [])]
                    cpath.write_text(json.dumps({
                        "merge_base": data["merge_base_commit"]["sha"], "head": node["headRefOid"],
                        "base_ref_oid": node["baseRefOid"], "files": files,
                        "files_truncated": len(data.get("files", [])) >= 300,
                        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}))
            compare = json.loads(cpath.read_text())
            if "error" in compare:
                continue
            hits = []
            for f in compare["files"]:
                if is_code(f["filename"]) and not is_test(f["filename"]):
                    if any(rx.search(t) for _, _, t in changed_lines(f.get("patch"))):
                        hits.append(f["filename"])
            bpath = WORK / "blobs" / f"{key}.json"
            if not bpath.exists() and hits:
                status = {f["filename"]: f["status"] for f in compare["files"]}
                wanted = hits[:10]
                requests = [((compare["merge_base"] if status.get(p) == "removed" else compare["head"]), p) for p in wanted]
                owner, name = owner_repo(node)
                texts = fetch_blobs(owner, name, requests)
                bpath.write_text(json.dumps({"prefilter": hits, "files": {p: texts.get(r) for p, r in zip(wanted, requests, strict=True)}}))
            if not hits:
                continue
            blobs = json.loads(bpath.read_text()) if bpath.exists() else {"files": {}}
            tags = set()
            for text in blobs["files"].values():
                if text:
                    tags |= set(framework_tags(import_lines(text)))
            if not (tags & TAG_FOR[framework]):
                continue
            flagged += 1
            per_repo[repo] = per_repo.get(repo, 0) + 1
            chunks.append(dossier_b(node, index, compare, blobs, rx))
        out = WORK / "dossiers" / f"b_{framework}.txt"
        out.write_text("\n\n".join(chunks) + "\n")
        (WORK / f"walk_b_{framework}.json").write_text(json.dumps({"walked": walked, "flagged": flagged,
                                                                   "last_index": index, "candidates": len(nodes)}))
        print(framework, "walked", walked, "of", len(nodes), "flagged", flagged, flush=True)


def dossier_b(node: dict, index: int, compare: dict, blobs: dict, rx: re.Pattern) -> str:
    state = "merged" if node["merged"] else node["state"].lower()
    files = compare["files"]
    code = [f for f in files if is_code(f["filename"]) and not is_test(f["filename"])]
    lines = [f"### [{index}] {node['url']}  ({state}{', fork' if node['repository']['isFork'] else ''}) {node['createdAt'][:10]}",
             f"title: {node['title'][:150]}",
             f"pins: merge_base {compare['merge_base']} head {compare['head']}",
             f"files: {len(files)} | code: " + ", ".join(
                 f"{f['filename']}({f['status'][0]} +{f['additions']}/-{f['deletions']})" for f in code[:6])
             + (f" ...(+{len(code) - 6})" if len(code) > 6 else "")]
    shown = 0
    for f in code:
        if shown >= 30:
            break
        changed = [c for c in changed_lines(f.get("patch")) if rx.search(c[2])]
        if not changed:
            continue
        text = (blobs["files"].get(f["filename"]) or "")
        src = text.splitlines()
        lines.append(f"  -- {f['filename']}")
        for sign, num, t in changed[:6]:
            lines.append(f"   {sign}{num}: {t.strip()[:140]}")
            shown += 1
            if sign == "+" and src and 0 < num <= len(src):
                lo, hi = max(1, num - 3), min(len(src), num + 3)
                ctx = " | ".join(s.strip()[:70] for s in src[lo - 1: hi] if s.strip())
                lines.append(f"      ctx[{lo}-{hi}]: {ctx[:420]}")
    return "\n".join(lines)


def main() -> None:
    global WORK
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", type=Path, required=True, help="directory for every intermediate file")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("frame-a")
    a.add_argument("--start", type=int, default=0)
    a.add_argument("--slices", type=int, default=16)
    a.set_defaults(func=cmd_frame_a)
    b = sub.add_parser("frame-b")
    b.add_argument("--only", default="")
    b.set_defaults(func=cmd_frame_b)
    w = sub.add_parser("b-walk")
    w.add_argument("--only", default="")
    w.add_argument("--limit", type=int, default=60)
    w.set_defaults(func=cmd_b_walk)
    for name, func in (("candidates", cmd_candidates), ("fetch", cmd_fetch), ("blobs", cmd_blobs),
                       ("dossier", cmd_dossier)):
        p = sub.add_parser(name)
        p.add_argument("--frame", default="a")
        p.add_argument("--start", type=int, default=0)
        p.add_argument("--limit", type=int, default=100000)
        p.set_defaults(func=func)
    args = parser.parse_args()
    WORK = args.work.resolve()
    (WORK / "dossiers").mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
