"""An independent census of host-configuration paths.

Written from the hosts' own published configuration surfaces, deliberately
NOT from ``agents_shipgate.core.boundary_registry``. That independence is
the whole point: classifying a repository's history with the engine's own
registry makes every path the engine does not read look like a benign
change with nothing to report, so a coverage gap and a correct silence
become the same measurement.

Run it against a harness result to get the disagreement count:

    python benchmark/cold-start/census.py steps.jsonl clones/

Two kinds of disagreement, and they mean opposite things:

* **a path the census names and the engine did not read** — a coverage
  gap. Every such step's zero rows are silence, not correctness.
* **a path the engine read and the census does not name** — a census bug.
  The list below is behind the registry, or names something that is not
  host surface.

Compared per path rather than per step: a step where each side names a
different file is two findings, not agreement.

Both are printed. Neither is allowed to be invisible.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

#: Path patterns each host documents as configuration it reads. Sources are
#: the hosts' own docs, not this engine.
CENSUS: tuple[str, ...] = (
    # Claude Code
    ".claude/settings.json", ".claude/settings.local.json",
    ".claude/hooks/hooks.json", "**/.claude/hooks/hooks.json",
    ".claude/hooks/*",  # the scripts those commands run — see KNOWN_GAPS
    ".claude/commands/*", ".claude/commands/**",
    ".claude/agents/*", ".claude/skills/**",
    "CLAUDE.md", "**/CLAUDE.md", ".claude/CLAUDE.md",
    ".mcp.json", "**/mcp.json",
    # Codex
    ".codex/config.toml", ".codex/hooks.json", ".codex/requirements.toml",
    "**/.codex/config.toml", "**/.codex/hooks.json",
    "AGENTS.md", "**/AGENTS.md", "codex.md",
    "AGENTS.override.md", "**/AGENTS.override.md",
    # Cursor
    ".cursor/mcp.json", ".cursor/cli-config.json", ".cursor/cli.json",
    ".cursor/rules/*", ".cursor/rules/**", ".cursorrules",
    # Portable skills
    ".agents/skills/*/SKILL.md", ".agents/skills/**",
    # VS Code / Copilot
    ".vscode/mcp.json", ".github/copilot-instructions.md",
    ".github/instructions/*", ".github/prompts/*",
    # Windsurf, Gemini, Aider
    ".windsurfrules", ".windsurf/**", ".gemini/*", "GEMINI.md", ".aider.conf.yml",
    # GitHub Actions: the token permissions an agent's CI runs with
    ".github/workflows/*.yml", ".github/workflows/*.yaml",
    ".github/actions/*/action.yml", ".github/actions/*/action.yaml",
    # Shipgate's own declared surface: a policy or contract that governs the
    # review is as much part of the boundary as the host files it reads.
    "shipgate.yaml", ".agents-shipgate/*",
    "policies/*.shipgate.yaml", ".shipgate/agent-contract.json",
)

#: Patterns whose files are host surface only when they carry particular
#: keys, which a census of *paths* cannot determine. Counting them as host
#: surface over-counts: `DanielLavrushin/b4` changed `.vscode/settings.json`
#: to set `editor.formatOnSave` and `files.eol`, which is not a capability
#: change by any reading. Left out, with the reason recorded, rather than
#: silently dropped.
CONDITIONAL_ON_CONTENT: tuple[str, ...] = (
    ".vscode/settings.json",  # host surface only with an `mcp` or agent key
)

#: Census paths the engine is known not to read yet, with the issue that
#: owns each. A disagreement on one of these is expected and is reported
#: separately from an unexplained one, so a new gap cannot hide among them.
KNOWN_GAPS: dict[str, str] = {
    ".github/actions/*/action.yml": "#701",
    ".github/actions/*/action.yaml": "#701",
    # The declaration is read; the script a hook command names is not, so
    # editing it changes what runs with no row.
    ".claude/hooks/*": "#702",
}


def census_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if any(fnmatch.fnmatch(p, g) for g in CENSUS)]


#: Paths a KNOWN_GAPS pattern would otherwise swallow but which the engine
#: does read. Without this, `.claude/hooks/*` would file `hooks.json` under
#: #702 and a regression that stopped reading it would be reported as a
#: known gap instead of a new one.
READ_DESPITE_GAP_PATTERN: frozenset[str] = frozenset({
    ".claude/hooks/hooks.json",
})


def known_gap(path: str) -> str | None:
    if Path(path).name in {Path(p).name for p in READ_DESPITE_GAP_PATTERN}:
        return None
    for pattern, issue in KNOWN_GAPS.items():
        if fnmatch.fnmatch(path, pattern):
            return issue
    return None


def main(steps_file: Path, clones: Path) -> int:
    steps = [json.loads(line) for line in steps_file.read_text().splitlines() if line.strip()]
    agree = 0
    gaps: Counter[str] = Counter()
    explained: Counter[str] = Counter()
    census_bugs: Counter[str] = Counter()
    for step in steps:
        repo = clones / step["repo"].replace("/", "__")
        changed = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", step["parent"], step["child"]],
            capture_output=True, text=True,
        ).stdout.split("\n")
        by_census = set(census_paths([p for p in changed if p]))
        by_engine = set(step["host_files"])
        if by_census == by_engine:
            agree += 1
            continue
        # Compared as sets, not as "did either find anything". A step where
        # the census names one path and the engine names a different one is
        # two findings, and counting it as agreement hides both.
        for path in sorted(by_census - by_engine):
            issue = known_gap(path)
            (explained if issue else gaps)[f"{path} {issue or ''}".strip()] += 1
        for path in sorted(by_engine - by_census):
            census_bugs[path] += 1

    print(f"steps={len(steps)} agree={agree} "
          f"unexplained_gaps={sum(gaps.values())} "
          f"known_gaps={sum(explained.values())} "
          f"census_bugs={sum(census_bugs.values())}")
    for title, counter in (
        ("UNEXPLAINED COVERAGE GAP (census says host, engine scored benign)", gaps),
        ("known gap, issue-owned", explained),
        ("CENSUS BUG (engine says host, census says benign)", census_bugs),
    ):
        if counter:
            print(f"\n{title}:")
            for path, count in counter.most_common(20):
                print(f"  {count:4}x {path}")
    # Only an unexplained gap or a census bug is a failure. A gap with an
    # issue is a number someone is already answerable for.
    return 1 if gaps or census_bugs else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
