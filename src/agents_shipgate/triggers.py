"""Evaluate the published trigger catalog against a snapshot of repo state.

The catalog (``docs/triggers.json``) is the machine-readable mirror of the
AGENTS.md trigger table. A coding agent that has not yet adopted Shipgate
can fetch ``triggers.json`` and apply the rules against a PR diff or repo
state to decide whether to propose ``agents-shipgate detect`` as the next
step, without parsing prose.

This module is the canonical evaluator. It exists primarily so:

- repo developers can verify the rules locally
  (``python -m agents_shipgate.triggers --paths a.py b.json``)
- the public-surface contract test asserts AGENTS.md ↔ triggers.json
  consistency through a real loader rather than re-parsing JSON in pytest

The rule schema and predicate vocabulary are stable for 0.x: rule IDs,
predicate names, and action enum values do not change in minor versions.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

_TRIGGERS_FILENAME = "triggers.json"

# Action precedence for the evaluator. Highest first:
#
#   stop_conditions → skip
#   force_run       → run (used by TRIGGER-EXISTING-MANIFEST-PRESENT;
#                     overrides skip because an opted-in repo always runs)
#   skip_shipgate   → skip (a docs-only PR with no opt-in cannot be
#                     overridden by a brittle diff_contains match)
#   run_shipgate    → run
#   dry_run         → skip+dry_run_recommended (advisory, not a run)
#   no rules        → skip
ACTION_FORCE_RUN = "force_run"
ACTION_RUN = "run_shipgate"
ACTION_SKIP = "skip_shipgate"
ACTION_DRY_RUN = "dry_run"
VALID_ACTIONS = frozenset(
    {ACTION_FORCE_RUN, ACTION_RUN, ACTION_SKIP, ACTION_DRY_RUN}
)


def load_triggers() -> dict[str, Any]:
    """Return the trigger catalog as a dict.

    Tries the wheel-bundled location first
    (``agents_shipgate/_meta/triggers.json``) and falls back to a
    repo-relative ``docs/triggers.json`` for editable installs and
    source checkouts. Mirrors :func:`agents_shipgate.fixtures.fixtures_root`.
    """
    try:
        bundled = files("agents_shipgate") / "_meta" / _TRIGGERS_FILENAME
        if bundled.is_file():
            return json.loads(bundled.read_text(encoding="utf-8"))
    except (ModuleNotFoundError, FileNotFoundError):
        pass

    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        candidate = parent / "docs" / _TRIGGERS_FILENAME
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))

    raise FileNotFoundError(
        "triggers.json not found. Looked in the packaged "
        "agents_shipgate/_meta/ and ../docs/ relative to the source tree."
    )


def _glob_match(pattern: str, path: str) -> bool:
    """Match ``path`` against a glob extended with ``**`` semantics.

    ``**/foo`` matches ``foo`` at any depth (including the repo root);
    ``dir/**`` matches ``dir`` and anything below it; bare ``**``
    matches zero or more characters across path segments. ``*`` and
    ``?`` are segment-local (do not cross ``/``). Path separators are
    forward slashes; backslashes are normalized.
    """
    pattern = pattern.replace("\\", "/")
    path = path.replace("\\", "/")
    if not any(token in pattern for token in ("*", "?", "[")):
        return path == pattern

    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            parts.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("/**", i):
            parts.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                parts.append(re.escape(pattern[i]))
                i += 1
            else:
                parts.append(pattern[i : close + 1])
                i = close + 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(parts), path) is not None


def _eval_predicate(
    pred: dict[str, Any] | None,
    *,
    paths: list[str],
    diff_text: str,
    manifest_present: bool,
    detect_result: dict[str, Any] | None,
    user_requested: bool,
) -> bool:
    if not pred:
        return False

    if "any_of" in pred:
        return any(
            _eval_predicate(
                p,
                paths=paths,
                diff_text=diff_text,
                manifest_present=manifest_present,
                detect_result=detect_result,
                user_requested=user_requested,
            )
            for p in pred["any_of"]
        )
    if "all_of" in pred:
        return all(
            _eval_predicate(
                p,
                paths=paths,
                diff_text=diff_text,
                manifest_present=manifest_present,
                detect_result=detect_result,
                user_requested=user_requested,
            )
            for p in pred["all_of"]
        )
    if "glob" in pred:
        return any(_glob_match(pred["glob"], p) for p in paths)
    if "diff_contains" in pred:
        return pred["diff_contains"] in diff_text
    if "every_file_matches" in pred:
        if not paths:
            return False
        patterns = pred["every_file_matches"]
        if isinstance(patterns, str):
            patterns = [patterns]
        return all(
            any(_glob_match(g, p) for g in patterns) for p in paths
        )
    if "none_match_glob" in pred:
        globs = pred["none_match_glob"]
        if isinstance(globs, str):
            globs = [globs]
        return not any(_glob_match(g, p) for g in globs for p in paths)
    if "file_present" in pred:
        return pred["file_present"] == "shipgate.yaml" and manifest_present
    if "file_absent" in pred:
        return pred["file_absent"] == "shipgate.yaml" and not manifest_present
    if "detect_returns" in pred:
        if detect_result is None:
            return False
        target = pred["detect_returns"]
        if ":" not in target:
            return False
        key, _, val = target.partition(":")
        actual = detect_result.get(key.strip())
        val = val.strip()
        if val == "false":
            return actual is False
        if val == "true":
            return actual is True
        if val == "[]":
            return actual == []
        return False
    if "user_did_not_request" in pred:
        return not user_requested
    return False


def evaluate(
    *,
    paths: list[str] | None = None,
    diff_text: str = "",
    manifest_present: bool = False,
    detect_result: dict[str, Any] | None = None,
    user_requested: bool = False,
    triggers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the trigger catalog against a snapshot of repo state.

    Returns a dict with:

    - ``run_shipgate`` (bool) — final verdict.
    - ``matched_rules`` (list) — every rule whose ``when`` clause fired.
    - ``stop_conditions_fired`` (bool) — whether the explicit stop
      block held; this beats every rule action.
    - ``dry_run_recommended`` (bool) — true when a ``dry_run`` rule
      fired and no ``run_shipgate``/``force_run``/``skip_shipgate``
      rule did. Callers that want to be helpful can propose a
      non-mutating ``scan`` even though ``run_shipgate`` is false.
    - ``rationale`` (str) — single-sentence explanation.
    - ``schema_version`` (str) — the trigger catalog's schema version.

    Action precedence (highest first): ``stop_conditions`` → skip;
    ``force_run`` → run (overrides skip; used by manifest-present);
    ``skip_shipgate`` → skip (beats ``run_shipgate``); ``run_shipgate``
    → run; ``dry_run`` → skip + ``dry_run_recommended``.
    """
    if triggers is None:
        triggers = load_triggers()
    paths = paths or []

    matched: list[dict[str, Any]] = []
    for rule in triggers.get("rules", []):
        when = rule.get("when")
        if _eval_predicate(
            when,
            paths=paths,
            diff_text=diff_text,
            manifest_present=manifest_present,
            detect_result=detect_result,
            user_requested=user_requested,
        ):
            matched.append(
                {
                    "id": rule["id"],
                    "action": rule["action"],
                    "rationale": rule.get("rationale", ""),
                    "command": rule.get("command"),
                }
            )

    stop_block = triggers.get("stop_conditions") or {}
    stop_payload = {k: v for k, v in stop_block.items() if k != "description"}
    stop_fired = bool(stop_payload) and _eval_predicate(
        stop_payload,
        paths=paths,
        diff_text=diff_text,
        manifest_present=manifest_present,
        detect_result=detect_result,
        user_requested=user_requested,
    )

    actions = [m["action"] for m in matched]
    has_force_run = any(a == ACTION_FORCE_RUN for a in actions)
    has_skip = any(a == ACTION_SKIP for a in actions)
    has_run = any(a == ACTION_RUN for a in actions)
    has_dry_run = any(a == ACTION_DRY_RUN for a in actions)

    dry_run_recommended = False
    if stop_fired:
        run = False
        rationale = (
            "Stop conditions hold (detect classifies as non-agent, "
            "no manifest, user did not explicitly request a scan)."
        )
    elif has_force_run:
        forcing = [m["id"] for m in matched if m["action"] == ACTION_FORCE_RUN]
        run = True
        rationale = (
            "force_run rule(s) overrode any skip: "
            f"{', '.join(forcing)}."
        )
    elif has_skip:
        run = False
        skipping = [m["id"] for m in matched if m["action"] == ACTION_SKIP]
        rationale = (
            "skip_shipgate rule(s) matched (beats run_shipgate): "
            f"{', '.join(skipping)}."
        )
    elif has_run:
        run = True
        run_count = sum(1 for a in actions if a == ACTION_RUN)
        rationale = f"{run_count} run_shipgate rule(s) matched."
    elif has_dry_run:
        run = False
        dry_run_recommended = True
        dry = [m["id"] for m in matched if m["action"] == ACTION_DRY_RUN]
        rationale = (
            "dry_run rule(s) matched (advisory, no manifest write): "
            f"{', '.join(dry)}."
        )
    else:
        run = False
        rationale = (
            "No rules matched; nothing in this PR signals a tool-surface change."
        )

    return {
        "run_shipgate": run,
        "dry_run_recommended": dry_run_recommended,
        "matched_rules": matched,
        "stop_conditions_fired": stop_fired,
        "rationale": rationale,
        "schema_version": triggers.get("schema_version"),
    }


def _git_diff_context(revspec: str | None) -> tuple[list[str], str]:
    """Read changed paths and the unified-diff body from ``git diff``.

    ``revspec`` semantics:

    - Non-empty (e.g. ``"origin/main...HEAD"``): PR-style diff.
      ``git diff [--name-only] <revspec>``.
    - Empty string (bare ``--git-diff``): all uncommitted tracked
      changes against ``HEAD`` — includes BOTH staged and unstaged
      edits. Untracked file *paths* (newly-`git add`-able files that
      aren't yet `git add`ed) are appended to the path list via
      ``git ls-files --others --exclude-standard``; their content is
      NOT captured in ``diff_text`` because reading arbitrary unstaged
      files into memory is risky.

    Returns ``([paths], diff_text)``.
    """
    if revspec:
        names_cmd = ["git", "diff", "--name-only", revspec]
        body_cmd = ["git", "diff", revspec]
    else:
        names_cmd = ["git", "diff", "HEAD", "--name-only"]
        body_cmd = ["git", "diff", "HEAD"]
    names = subprocess.run(names_cmd, capture_output=True, text=True, check=True)
    body = subprocess.run(body_cmd, capture_output=True, text=True, check=True)
    paths = [line for line in names.stdout.splitlines() if line.strip()]
    diff_text = body.stdout

    if not revspec:
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in untracked.stdout.splitlines():
            stripped = line.strip()
            if stripped and stripped not in paths:
                paths.append(stripped)
    return paths, diff_text


def _read_paths_from_stdin() -> list[str]:
    if sys.stdin.isatty():
        return []
    return [line.strip() for line in sys.stdin if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents_shipgate.triggers",
        description=(
            "Evaluate the agents-shipgate trigger catalog "
            "(docs/triggers.json) against a list of changed file paths "
            "and emit a run/skip verdict."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "Changed file paths (repo-relative, forward slashes). When "
            "omitted, newline-separated paths are read from stdin."
        ),
    )
    parser.add_argument(
        "--manifest-present",
        action="store_true",
        help="Treat shipgate.yaml as present in the workspace.",
    )
    parser.add_argument(
        "--user-requested",
        action="store_true",
        help=(
            "The user explicitly asked for a Shipgate run "
            "(suppresses the stop_conditions block)."
        ),
    )
    parser.add_argument(
        "--diff-text",
        default="",
        help=(
            "Optional unified-diff body. Used for `diff_contains` "
            "predicates (e.g. matching `@function_tool`). Ignored when "
            "--git-diff is also passed."
        ),
    )
    parser.add_argument(
        "--git-diff",
        nargs="?",
        const="",
        default=None,
        metavar="REVSPEC",
        help=(
            "Read changed paths AND the unified-diff body from "
            "`git diff [REVSPEC]`. Bare flag uses uncommitted changes; "
            "pass a revspec like `origin/main...HEAD` for a PR-style "
            "diff. Overrides positional paths, stdin paths, and "
            "--diff-text. Required for diff_contains rules to fire "
            "(e.g. @function_tool decorators)."
        ),
    )
    parser.add_argument(
        "--list-rules",
        action="store_true",
        help="Print the loaded rule catalog and exit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output. Default: human-readable summary.",
    )
    args = parser.parse_args(argv)

    triggers = load_triggers()

    if args.list_rules:
        if args.json:
            print(json.dumps(triggers, indent=2))
        else:
            for rule in triggers.get("rules", []):
                print(
                    f"{rule['id']}\t{rule['action']}\t"
                    f"{rule.get('rationale', '')}"
                )
        return 0

    if args.git_diff is not None:
        try:
            paths, diff_text = _git_diff_context(args.git_diff)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            print(
                f"--git-diff failed: {exc}. Run from a git checkout, or "
                "pass paths and --diff-text manually.",
                file=sys.stderr,
            )
            return 2
    else:
        paths = args.paths or _read_paths_from_stdin()
        diff_text = args.diff_text

    result = evaluate(
        paths=paths,
        diff_text=diff_text,
        manifest_present=args.manifest_present,
        user_requested=args.user_requested,
        triggers=triggers,
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    verdict = "RUN" if result["run_shipgate"] else "SKIP"
    print(f"Verdict: {verdict}")
    print(f"Rationale: {result['rationale']}")
    if result["matched_rules"]:
        print("Matched rules:")
        for m in result["matched_rules"]:
            cmd = f" → {m['command']}" if m.get("command") else ""
            print(f"  - {m['id']} [{m['action']}]{cmd}")
            if m.get("rationale"):
                print(f"      {m['rationale']}")
    if result["stop_conditions_fired"]:
        print("Stop conditions fired (overriding any matched rules).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
