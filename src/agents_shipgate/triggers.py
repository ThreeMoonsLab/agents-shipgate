"""Evaluate the published trigger catalog against a snapshot of repo state.

The catalog (``docs/triggers.json``) is the machine-readable mirror of the
AGENTS.md trigger table. A coding agent that has not yet adopted Shipgate
can fetch ``triggers.json`` and apply the rules against a PR diff or repo
state to decide whether to propose ``agents-shipgate verify --preview --json`` as
the next step, without parsing prose.

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
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from agents_shipgate.core.boundary_registry import (
    boundary_adapters_for_path,
    is_agent_boundary_path,
)
from agents_shipgate.core.globbing import glob_match as _glob_match

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

# Semantic class of the surface a rule describes. Rule IDs are stable audit
# labels, not a type system: consumers must switch on ``surface_class`` instead
# of maintaining ID allow-lists that silently miss newly-added adapters.
SURFACE_CLASS_CAPABILITY = "capability"
SURFACE_CLASS_HOST_BOUNDARY = "host_boundary"
VALID_SURFACE_CLASSES = frozenset(
    {
        SURFACE_CLASS_CAPABILITY,
        SURFACE_CLASS_HOST_BOUNDARY,
        "governance",
        "adoption",
        "dependency",
        "negative",
    }
)


def result_has_surface_class(result: dict[str, Any], surface_class: str) -> bool:
    """Return whether any matched rule carries ``surface_class``.

    Callers should separately honor the evaluator's winning action. A negative
    rule can match alongside a positive rule, so this helper deliberately does
    not reinterpret action precedence.
    """

    return any(
        match.get("surface_class") == surface_class
        for match in result.get("matched_rules", [])
        if isinstance(match, dict)
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


def _collect_diff_contains(pred: Any, out: set[str]) -> None:
    """Walk a predicate tree and collect every ``diff_contains`` token."""
    if not isinstance(pred, dict):
        return
    for key in ("any_of", "all_of"):
        if key in pred:
            for nested in pred[key]:
                _collect_diff_contains(nested, out)
    token = pred.get("diff_contains")
    if isinstance(token, str):
        out.add(token)


def _matched_diff_tokens(triggers: dict[str, Any], diff_text: str) -> list[str]:
    """Return the catalog ``diff_contains`` tokens present in ``diff_text``.

    Deterministic (sorted, de-duplicated). These are the stable token
    forms — decorator names, package names, function calls — that the
    catalog watches for and that actually appear in the supplied diff.
    """
    if not diff_text:
        return []
    tokens: set[str] = set()
    for rule in triggers.get("rules", []):
        _collect_diff_contains(rule.get("when"), tokens)
    stop_block = triggers.get("stop_conditions") or {}
    _collect_diff_contains(
        {k: v for k, v in stop_block.items() if k != "description"}, tokens
    )
    return sorted(token for token in tokens if token in diff_text)


def _contains_detect_returns(pred: Any) -> bool:
    """Whether a predicate tree contains any ``detect_returns`` leaf.

    Used to decide whether the stop block can be *fully evaluated*: a
    ``detect_returns`` predicate needs the output of ``agents-shipgate
    detect``. When that output was not supplied, the stop block is not
    evaluable and the evaluator must not infer a stop verdict.
    """
    if not isinstance(pred, dict):
        return False
    if "detect_returns" in pred:
        return True
    return any(
        _contains_detect_returns(nested)
        for key in ("any_of", "all_of")
        if key in pred
        for nested in pred[key]
    )


def _next_action(
    *,
    run: bool,
    dry_run_recommended: bool,
    skip_reason: str | None,
    manifest_present: bool,
    matched: list[dict[str, Any]],
    default_command: str,
    rationale: str,
) -> dict[str, Any]:
    """Synthesize the single recommended next step from the verdict.

    Deterministic projection of the run/skip decision into an actor-
    agnostic ``{kind, command, why}``. Adopted repos (a manifest is
    present) are pointed at ``verify`` — the canonical ongoing-PR gate;
    un-adopted repos are pointed at the catalog verify-preview command so a
    coding agent can route setup. ``command`` is ``None`` when no action
    is warranted.
    """
    if run:
        if manifest_present:
            return {
                "kind": "command",
                "command": "agents-shipgate verify --base origin/main --head HEAD --json",
                "why": (
                    "This change affects an agent tool or release-policy "
                    "surface; verify whether the PR can merge."
                ),
            }
        command = next(
            (m["command"] for m in matched if m.get("command")), None
        ) or default_command
        return {
            "kind": "command",
            "command": command,
            "why": (
                "This change looks agent-related; start with verify preview "
                "and adopt Shipgate if the preview routes setup."
            ),
        }
    if dry_run_recommended:
        command = (
            "agents-shipgate verify --base origin/main --head HEAD "
            "--ci-mode advisory --json"
            if manifest_present
            else default_command
        )
        return {
            "kind": "command",
            "command": command,
            "why": (
                "A framework/runtime bump can shift the tool surface; run "
                "an advisory check without writing a manifest."
            ),
        }
    if skip_reason == "stop_conditions":
        return {"kind": "stop", "command": None, "why": rationale}
    return {"kind": "none", "command": None, "why": rationale}


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
    if "boundary_adapter" in pred:
        adapter_id = pred["boundary_adapter"]
        return any(
            any(adapter.id == adapter_id for adapter in boundary_adapters_for_path(path))
            for path in paths
        )
    if "none_match_boundary_surface" in pred:
        return bool(pred["none_match_boundary_surface"]) and not any(
            is_agent_boundary_path(path) for path in paths
        )
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

    - ``schema_version`` (str) — the trigger catalog's schema version.
    - ``should_run`` (bool) — friendly alias of ``run_shipgate`` (same
      value); kept so consumers reading either field agree.
    - ``run_shipgate`` (bool) — final verdict.
    - ``force_run`` (bool) — a ``force_run`` rule matched and was not
      overridden by the stop block (opted-in repo → run on every PR).
    - ``dry_run_recommended`` (bool) — true when a ``dry_run`` rule
      fired and no ``run_shipgate``/``force_run``/``skip_shipgate``
      rule did. Callers that want to be helpful can propose a
      non-mutating ``scan`` even though ``run_shipgate`` is false.
    - ``skip`` (bool) — inverse of ``should_run``; convenience for
      consumers that branch on the skip case.
    - ``skip_reason`` (str|None) — ``None`` when running; otherwise a
      stable token: ``stop_conditions``, ``skip_rule``, ``dry_run_only``
      or ``no_match``.
    - ``stop_conditions_fired`` (bool) — whether the explicit stop
      block held; this beats every rule action.
    - ``stop_conditions_evaluated`` (bool) — whether the stop block
      could be fully evaluated. ``False`` when the block references
      ``detect_returns`` but no ``detect_result`` was supplied; in that
      case the evaluator never stops (``stop_conditions_fired`` stays
      ``False``) and the caller knows the stop verdict is unknown rather
      than "evaluated and did not hold".
    - ``rationale`` (str) — single-sentence explanation.
    - ``matched_rules`` (list) — every rule whose ``when`` clause fired.
    - ``changed_files`` (list) — the input paths, echoed back.
    - ``diff_tokens`` (list) — catalog ``diff_contains`` tokens that
      are present in ``diff_text`` (sorted, de-duplicated).
    - ``next_action`` (dict) — the single recommended next step as
      ``{kind, command, why}`` (``kind`` is ``command``/``stop``/
      ``none``); a deterministic projection of the verdict.

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
                    "surface_class": rule.get("surface_class"),
                    "rationale": rule.get("rationale", ""),
                    "command": rule.get("command"),
                }
            )

    stop_block = triggers.get("stop_conditions") or {}
    stop_payload = {k: v for k, v in stop_block.items() if k != "description"}
    # The stop block can only be trusted when it is fully evaluable. If it
    # references detect output (detect_returns) but none was supplied, we
    # cannot conclude "non-agent project" — so we never stop on it, and we
    # report stop_conditions_evaluated=False so consumers can tell the
    # difference between "evaluated, did not hold" and "could not evaluate".
    stop_conditions_evaluated = bool(stop_payload) and (
        detect_result is not None or not _contains_detect_returns(stop_payload)
    )
    stop_fired = stop_conditions_evaluated and _eval_predicate(
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
    skip_reason: str | None = None
    if stop_fired:
        run = False
        skip_reason = "stop_conditions"
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
        skip_reason = "skip_rule"
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
        skip_reason = "dry_run_only"
        dry = [m["id"] for m in matched if m["action"] == ACTION_DRY_RUN]
        rationale = (
            "dry_run rule(s) matched (advisory, no manifest write): "
            f"{', '.join(dry)}."
        )
    else:
        run = False
        skip_reason = "no_match"
        rationale = (
            "No rules matched; nothing in this PR signals a tool-surface change."
        )

    # ``should_run`` is a friendlier alias of ``run_shipgate`` (identical
    # value); both are kept so 0.x consumers reading either field agree.
    next_action = _next_action(
        run=run,
        dry_run_recommended=dry_run_recommended,
        skip_reason=skip_reason,
        manifest_present=manifest_present,
        matched=matched,
        default_command=triggers.get(
            "default_command", "agents-shipgate verify --preview --json"
        ),
        rationale=rationale,
    )
    return {
        "schema_version": triggers.get("schema_version"),
        "should_run": run,
        "run_shipgate": run,
        "skip": not run,
        "force_run": has_force_run and not stop_fired,
        "dry_run_recommended": dry_run_recommended,
        "skip_reason": skip_reason,
        "stop_conditions_fired": stop_fired,
        "stop_conditions_evaluated": stop_conditions_evaluated,
        "rationale": rationale,
        "matched_rules": matched,
        "changed_files": list(paths),
        "diff_tokens": _matched_diff_tokens(triggers, diff_text),
        "next_action": next_action,
    }


def _git_diff_context(
    revspec: str | None, *, cwd: Path | None = None
) -> tuple[list[str], str]:
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

    ``cwd`` selects the git working directory; ``None`` uses the
    process cwd.

    Returns ``([paths], diff_text)``.
    """
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "check": True,
    }
    if cwd is not None:
        run_kwargs["cwd"] = str(cwd)
    if revspec:
        names_cmd = ["git", "diff", "--name-only", revspec]
        body_cmd = ["git", "diff", revspec]
    else:
        names_cmd = ["git", "diff", "HEAD", "--name-only"]
        body_cmd = ["git", "diff", "HEAD"]
    names = subprocess.run(names_cmd, **run_kwargs)
    body = subprocess.run(body_cmd, **run_kwargs)
    paths = [line for line in names.stdout.splitlines() if line.strip()]
    diff_text = body.stdout

    if not revspec:
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            **run_kwargs,
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
        "--detect-json",
        default=None,
        metavar="PATH",
        help=(
            "Path to a saved `agents-shipgate detect --json` result. "
            "Supplies the detect_result the stop_conditions block needs; "
            "without it the stop block is reported as not evaluated."
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

    detect_result: dict[str, Any] | None = None
    if args.detect_json is not None:
        try:
            detect_result = json.loads(
                Path(args.detect_json).read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(
                f"--detect-json could not be read: {exc}.",
                file=sys.stderr,
            )
            return 2

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
        detect_result=detect_result,
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
    next_action = result["next_action"]
    if next_action.get("command"):
        print(f"Next: {next_action['command']}")
    if result["stop_conditions_fired"]:
        print("Stop conditions fired (overriding any matched rules).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
