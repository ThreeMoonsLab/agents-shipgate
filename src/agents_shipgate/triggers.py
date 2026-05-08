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
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

_TRIGGERS_FILENAME = "triggers.json"


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
        return all(_glob_match(pred["every_file_matches"], p) for p in paths)
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
      block held; this overrides any matched ``run_shipgate`` rule.
    - ``rationale`` (str) — single-sentence explanation.
    - ``schema_version`` (str) — the trigger catalog's schema version.

    ``run_shipgate`` is true when at least one ``run_shipgate`` rule
    fires, no ``skip_shipgate`` rule fires, and ``stop_conditions`` does
    not hold. ``dry_run`` rules do not by themselves flip the verdict
    but appear in ``matched_rules`` so callers can choose to act on them.
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
    has_run = any(a == "run_shipgate" for a in actions)
    has_skip = any(a == "skip_shipgate" for a in actions)

    if stop_fired:
        run = False
        rationale = (
            "Stop conditions hold (detect classifies as non-agent, "
            "no manifest, user did not explicitly request a scan)."
        )
    elif has_skip and not has_run:
        run = False
        rationale = "skip_shipgate rule(s) matched and no run_shipgate rule fired."
    elif has_run:
        run_count = sum(1 for a in actions if a == "run_shipgate")
        run = True
        rationale = f"{run_count} run_shipgate rule(s) matched."
    else:
        run = False
        rationale = (
            "No rules matched; nothing in this PR signals a tool-surface change."
        )

    return {
        "run_shipgate": run,
        "matched_rules": matched,
        "stop_conditions_fired": stop_fired,
        "rationale": rationale,
        "schema_version": triggers.get("schema_version"),
    }


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
            "predicates (e.g. matching `@function_tool`)."
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

    paths = args.paths or _read_paths_from_stdin()
    result = evaluate(
        paths=paths,
        diff_text=args.diff_text,
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
