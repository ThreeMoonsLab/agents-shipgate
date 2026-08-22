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
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

from agents_shipgate.core.boundary_registry import (
    boundary_adapters_for_path,
    is_agent_boundary_path,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.globbing import glob_match as _glob_match_exact
from agents_shipgate.core.globbing import glob_match_ci as _glob_match
from agents_shipgate.invocation import render_command, retarget_command
from agents_shipgate.schemas.exclusions import (
    SurfaceExclusion,
    SurfaceExclusionLedger,
)

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

# How complete the diff evidence handed to the evaluator is. Mirrors
# ``BoundaryChangeSet.completeness`` and ``DiffContext.completeness`` so every
# input path in the product describes a partially-read diff the same way.
#
#   complete    — every changed path and the full diff body were read
#   partial     — some evidence is missing (typically: paths but no body)
#   unavailable — nothing about the change set was established
#
# Rule matching is monotone in path and diff evidence: adding evidence can only
# add matches. So a *run* verdict reached from incomplete evidence stays sound,
# while any *skip* verdict does not — the missing bytes are exactly what would
# have flipped it. That asymmetry is what ``evaluation_status`` reports.
INPUT_COMPLETE = "complete"
INPUT_PARTIAL = "partial"
INPUT_UNAVAILABLE = "unavailable"
VALID_INPUT_STATUSES = frozenset({INPUT_COMPLETE, INPUT_PARTIAL, INPUT_UNAVAILABLE})

EVALUATION_EVALUATED = "evaluated"
EVALUATION_NOT_EVALUATED = "not_evaluated"
# The inputs were read in full and no rule recognised any of them. That is a
# statement about the catalog, not about the PR, so it is not a skip.
#
# The asymmetry above says a skip reached from *unread* evidence is unsound.
# The same argument applies one level up, to evidence that was read and not
# understood: "no rules matched" is exactly as compatible with "this diff is
# irrelevant" as with "this diff carries a surface the catalog has no rule
# for". ``github/github-mcp-server#3076`` is the second one — a fully readable
# diff adding a destructive tool as
# ``pkg/github/__toolsnaps__/delete_repository.snap``, reported as "nothing in
# this PR signals a tool-surface change" because the MCP rule matches file
# names and that file is not named like one. A negative rule such as
# ``TRIGGER-DOCS-ONLY-NEGATIVE`` is a real skip: it classifies every changed
# file and concludes. ``no_match`` classifies nothing, so it now says so.
EVALUATION_UNCLASSIFIED = "unclassified"

#: Skip verdicts that a changed file nobody classified can invalidate.
#: ``stop_conditions`` is excluded on purpose — see the branch that reads this.
_UNCLASSIFIABLE_SKIP_REASONS = frozenset({"no_match", "skip_rule", "dry_run_only"})

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


DOCS_ONLY_RULE_ID = "TRIGGER-DOCS-ONLY-NEGATIVE"


def paths_without_capability_surface(paths: Sequence[str]) -> frozenset[str]:
    """Which of ``paths`` carry no capability surface on their own.

    The catalog's docs-only rule already answers "could this change alter an
    agent's capability surface?" for a whole change set. Applied to one path
    at a time it answers the same question about that path — including the
    carve-outs that make ``SKILL.md``, ``prompts/**``, and ``policies/**``
    capability surfaces despite looking like documentation.

    Manifest-scope resolution (:mod:`agents_shipgate.cli.discovery.scope`)
    needs exactly this, so it reads the rule rather than keeping a second
    list of documentation globs that could drift away from the one the
    ``trigger`` verdict is computed from.
    """

    rule = next(
        (
            entry
            for entry in load_triggers().get("rules", [])
            if isinstance(entry, dict) and entry.get("id") == DOCS_ONLY_RULE_ID
        ),
        None,
    )
    if rule is None:  # pragma: no cover - catalog is pinned by contract tests
        return frozenset()
    predicate = rule.get("when")
    return frozenset(
        path
        for path in paths
        if path
        and _eval_predicate(
            predicate,
            paths=[path],
            diff_text="",
            manifest_present=False,
            detect_result=None,
            user_requested=False,
        )
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


def _detect_returns_keys(pred: Any) -> set[str]:
    """Every ``DetectResult`` key a predicate tree reads.

    Used to decide whether the stop block can be *fully evaluated*: a
    ``detect_returns`` predicate needs the output of ``agents-shipgate
    detect``. When that output was not supplied — or was supplied by a build
    that does not carry one of the keys the block reads — the stop block is
    not evaluable and the evaluator must not infer a stop verdict.

    Per key rather than per payload, because "the payload is present" stopped
    being the same question as "the payload answers the block" the moment the
    block grew a key older payloads do not carry. Reading an absent key as
    ``false`` would resurrect the failure this whole block guards against, in
    the one direction that matters: silently concluding "not an agent
    project" from evidence nobody produced (#399 review).
    """
    if not isinstance(pred, dict):
        return set()
    keys: set[str] = set()
    target = pred.get("detect_returns")
    if isinstance(target, str) and ":" in target:
        keys.add(target.partition(":")[0].strip())
    for group in ("any_of", "all_of"):
        for nested in pred.get(group) or ():
            keys |= _detect_returns_keys(nested)
    return keys


def _next_action(
    *,
    run: bool | None,
    dry_run_recommended: bool,
    skip_reason: str | None,
    manifest_present: bool,
    matched: list[dict[str, Any]],
    default_command: str,
    rationale: str,
    evaluation_status: str = EVALUATION_EVALUATED,
) -> dict[str, Any]:
    """Synthesize the single recommended next step from the verdict.

    Deterministic projection of the run/skip decision into an actor-
    agnostic ``{kind, command, why}``. Adopted repos (a manifest is
    present) are pointed at ``verify`` — the canonical ongoing-PR gate;
    un-adopted repos are pointed at the catalog verify-preview command so a
    coding agent can route setup. ``command`` is ``None`` when no action
    is warranted.
    """
    if evaluation_status == EVALUATION_UNCLASSIFIED:
        # Nothing to repair: the diff was read in full. What is missing is a
        # verdict about content the catalog has no rule for, and the scan is
        # the thing that can produce one — so this routes forward rather than
        # back, which is the whole difference from the withheld case below.
        return {
            "kind": "command",
            "command": render_command(
                ["verify", "--base", "origin/main", "--head", "HEAD", "--json"]
            )
            if manifest_present
            else retarget_command(default_command),
            "why": (
                "The catalog classified none of the changed files, so it "
                "cannot say the change is irrelevant; let the scan decide."
            ),
        }
    if run is None:
        # The verdict was withheld because the diff was never read. The only
        # honest next step is to repair the input, and the caller that failed
        # to read it is the one that knows how — so no command is invented here.
        return {"kind": "input_required", "command": None, "why": rationale}
    if run:
        if manifest_present:
            return {
                "kind": "command",
                "command": render_command(
                    ["verify", "--base", "origin/main", "--head", "HEAD", "--json"]
                ),
                "why": (
                    "This change affects an agent tool or release-policy "
                    "surface; verify whether the PR can merge."
                ),
            }
        command = retarget_command(
            next((m["command"] for m in matched if m.get("command")), None)
            or default_command
        )
        return {
            "kind": "command",
            "command": command,
            "why": (
                "This change looks agent-related; start with verify preview "
                "and adopt Shipgate if the preview routes setup."
            ),
        }
    if dry_run_recommended:
        command = retarget_command(
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
    # Case sensitivity is chosen per predicate by which way a wider match
    # moves the verdict, not for uniformity:
    #
    # - ``glob`` (case-INsensitive). A wider match adds a *run*. A
    #   case-sensitive matcher would route
    #   ``services/foo/Policies/refund.yaml`` as ``no_match`` while
    #   ``SHIP-VERIFY-TRUST-ROOT-TOUCHED`` classifies the same path as a
    #   policy trust root.
    # - ``none_match_glob`` (case-INsensitive). It guards a negative rule,
    #   so a wider match makes that rule fire *less*. Case-folding it is what
    #   stops a ``Prompts/*.md`` edit skipping through the docs-only rule.
    # - ``every_file_matches`` (case-SENSITIVE, deliberately). It is the
    #   negative rule's own classifier, so a wider match makes the rule fire
    #   *more* and `skip_shipgate` beats `run_shipgate`. Case-folding it
    #   reads ``src/TEST_agent.py`` — a legitimate production path on a
    #   case-sensitive filesystem — as a test file, and a diff adding
    #   ``@function_tool`` beside it would be skipped rather than run.
    #
    # The rule: fold the predicates that can only add evaluation, never the
    # one that can subtract it. The catalog's ``predicate_vocabulary``
    # documents both, so third-party consumers apply the same semantics.
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
        # Case-sensitive on purpose — see the note on this function's
        # signature. Widening this predicate adds skips, not runs.
        return all(
            any(_glob_match_exact(g, p) for g in patterns) for p in paths
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
    input_status: str = INPUT_COMPLETE,
) -> dict[str, Any]:
    """Evaluate the trigger catalog against a snapshot of repo state.

    ``input_status`` declares how complete that snapshot is (see
    ``INPUT_COMPLETE`` / ``INPUT_PARTIAL`` / ``INPUT_UNAVAILABLE``). A caller
    that could not read the PR diff must say so: without it the evaluator sees
    an empty path list and an empty diff body, which are indistinguishable from
    a PR that genuinely changed nothing relevant, and it would report
    ``skip_reason: "no_match"`` — "nothing in this PR signals a tool-surface
    change" — about a PR it never read.

    Returns a dict with:

    - ``schema_version`` (str) — the trigger catalog's schema version.
    - ``input_status`` (str) — echoed back: ``complete``, ``partial`` or
      ``unavailable``.
    - ``evaluation_status`` (str) — ``evaluated`` when the verdict is
      supported by the evidence that was actually read; ``not_evaluated``
      exactly when the inputs were incomplete *and* the rules that did run
      produced no reason to run (that combination proves nothing, so no
      verdict is published); ``unclassified`` when the inputs *were* complete
      and still no rule matched any of the changed files. The last one is not
      a skip: the catalog recognised nothing, which is a fact about the
      catalog rather than about the PR. ``should_run`` is ``None`` for both
      withheld states, and ``surface_exclusions`` lists the change set the
      trigger could not account for.
    - ``should_run`` (bool|None) — friendly alias of ``run_shipgate`` (same
      value); kept so consumers reading either field agree. ``None`` whenever
      the verdict is withheld, i.e. ``evaluation_status`` is ``not_evaluated``
      or ``unclassified``.
    - ``run_shipgate`` (bool|None) — final verdict; ``None`` whenever the
      verdict is withheld.
    - ``force_run`` (bool) — a ``force_run`` rule matched and was not
      overridden by the stop block (opted-in repo → run on every PR).
    - ``dry_run_recommended`` (bool) — true when a ``dry_run`` rule
      fired and no ``run_shipgate``/``force_run``/``skip_shipgate``
      rule did. Callers that want to be helpful can propose a
      non-mutating ``scan`` even though ``run_shipgate`` is false.
    - ``skip`` (bool|None) — inverse of ``should_run``; convenience for
      consumers that branch on the skip case. ``None`` when not evaluated.
    - ``skip_reason`` (str|None) — ``None`` when running *and* when the
      verdict was withheld; otherwise a stable token: ``stop_conditions``,
      ``skip_rule``, ``dry_run_only`` or ``no_match``. ``no_match`` is
      never emitted for inputs that were not fully read, and never for a
      non-empty change set no rule classified — that is ``unclassified``,
      and it withholds the verdict too.
    - ``stop_conditions_fired`` (bool) — whether the explicit stop
      block held; this beats every rule action.
    - ``stop_conditions_evaluated`` (bool) — whether the stop block
      could be fully evaluated. ``False`` when the block references
      ``detect_returns`` but no ``detect_result`` was supplied, and
      ``False`` whenever ``input_status`` is not ``complete`` because the
      block reasons over the very path evidence that is missing. In those
      cases the evaluator never stops (``stop_conditions_fired`` stays
      ``False``) and the caller knows the stop verdict is unknown rather
      than "evaluated and did not hold".
    - ``rationale`` (str) — single-sentence explanation.
    - ``matched_rules`` (list) — every rule whose ``when`` clause fired.
    - ``changed_files`` (list) — the input paths, echoed back.
    - ``surface_exclusions`` (dict) — this stage's exclusion ledger
      (``{entries, total, gated, truncated}``, see
      ``agents_shipgate.schemas.exclusions``). Its entries are the changed
      files the trigger removed from analysis without classifying them;
      non-empty exactly when ``evaluation_status`` is ``unclassified``.
    - ``diff_tokens`` (list) — catalog ``diff_contains`` tokens that
      are present in ``diff_text`` (sorted, de-duplicated).
    - ``next_action`` (dict) — the single recommended next step as
      ``{kind, command, why}`` (``kind`` is ``command``/``stop``/
      ``none``/``input_required``); a deterministic projection of the
      verdict.

    Action precedence (highest first): ``stop_conditions`` → skip;
    ``force_run`` → run (overrides skip; used by manifest-present);
    ``skip_shipgate`` → skip (beats ``run_shipgate``); ``run_shipgate``
    → run; ``dry_run`` → skip + ``dry_run_recommended``. Incomplete input
    then withholds any resulting skip, and a complete-but-unrecognised
    change set withholds the ``no_match`` one.
    """
    if triggers is None:
        triggers = load_triggers()
    if input_status not in VALID_INPUT_STATUSES:
        raise ConfigError(
            f"Unknown trigger input_status {input_status!r}; expected one of "
            f"{sorted(VALID_INPUT_STATUSES)}."
        )
    paths = paths or []
    inputs_complete = input_status == INPUT_COMPLETE

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
            rule_command = rule.get("command")
            matched.append(
                {
                    "id": rule["id"],
                    "action": rule["action"],
                    "surface_class": rule.get("surface_class"),
                    "rationale": rule.get("rationale", ""),
                    # The bundled catalog spells commands as the installed
                    # console script. A matched row is a route for *this* run,
                    # not catalog documentation, so it is retargeted like every
                    # other emitted command.
                    "command": (
                        retarget_command(rule_command)
                        if isinstance(rule_command, str)
                        else rule_command
                    ),
                }
            )

    stop_block = triggers.get("stop_conditions") or {}
    stop_payload = {k: v for k, v in stop_block.items() if k != "description"}
    # The stop block can only be trusted when it is fully evaluable. If it
    # references detect output (detect_returns) but none was supplied, we
    # cannot conclude "non-agent project" — so we never stop on it, and we
    # report stop_conditions_evaluated=False so consumers can tell the
    # difference between "evaluated, did not hold" and "could not evaluate".
    required_detect_keys = _detect_returns_keys(stop_payload)
    stop_conditions_evaluated = (
        bool(stop_payload)
        and inputs_complete
        and (
            not required_detect_keys
            or (
                detect_result is not None
                and required_detect_keys <= set(detect_result)
            )
        )
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
    # A stop is terminal only while nothing contradicts it. The block's premise
    # is "this workspace is not an agent project", read from a `detect` pass
    # over the *worktree*; a matched run rule is evidence from the *diff*, and
    # the diff can carry what detect never saw. That is not hypothetical: a
    # `.snap` file holding an MCP tool schema is invisible to detect's
    # `suggested_sources` globs, so the whole stop block holds while
    # TRIGGER-MCP-TOOL-SCHEMA-CONTENT matches the same change — and the stop
    # discarded it, restoring the exact #403 fail-open in the pre-adoption flow
    # that supplies a complete negative detect result (PR #404 review).
    #
    # Only the run actions override it. `dry_run` is advisory by construction
    # and `skip_shipgate` agrees with the stop, so neither has anything to
    # contradict it with.
    stop_terminal = stop_fired and not (has_force_run or has_run)
    if stop_terminal:
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
        if stop_fired:
            rationale += (
                " Stop conditions also held and were overridden: a matched "
                "capability rule is diff evidence that the detector's "
                "whole-workspace negative did not account for."
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
        if stop_fired:
            rationale += (
                " Stop conditions also held and were overridden: a matched "
                "capability rule is diff evidence that the detector's "
                "whole-workspace negative did not account for."
            )
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

    verdict: bool | None = run
    evaluation_status = EVALUATION_EVALUATED
    unclassified: list[str] = []
    if not inputs_complete and not run:
        # Everything below the run verdicts rests on evidence that was never
        # read. "No rules matched" and "only docs changed" are claims about a
        # diff; without the diff they are claims about nothing. Withhold the
        # verdict rather than publish an unfalsifiable skip.
        verdict = None
        skip_reason = None
        # The advisory dry-run recommendation is derived from the same withheld
        # skip, so it is suppressed too. Nothing is lost: the rule that fired
        # is still listed in ``matched_rules``.
        dry_run_recommended = False
        evaluation_status = EVALUATION_NOT_EVALUATED
        missing = (
            "the change set could not be read, so there was no path or diff "
            "evidence to match against"
            if input_status == INPUT_UNAVAILABLE
            else "the change set was read only in part, so every rule that "
            "depends on the missing evidence could not fire"
        )
        rationale = (
            f"Trigger rules were not evaluated: {missing}. That is not "
            "evidence the PR is unrelated to agent capabilities — repair the "
            "diff input and re-evaluate."
        )
    elif skip_reason in _UNCLASSIFIABLE_SKIP_REASONS and (
        uncovered := _unclassified_paths(triggers, matched, paths)
    ):
        # Read in full, and some of it recognised by nothing. Every file here
        # is a subject the trigger removed from analysis without evidence, so
        # the skip resting on top of them is unfalsifiable. Withhold it and let
        # the scan decide.
        #
        # Scoped per path rather than per change set, because the dangerous
        # case is the mixed one: a dependency bump beside an opaque capability
        # file matched a `dry_run` rule that classified only the manifest, and
        # published an advisory skip over the sibling nobody read.
        #
        # `stop_conditions` is deliberately absent from the set. It is the one
        # skip backed by positive, falsifiable evidence — a `detect` payload
        # asserting the workspace is not an agent project — and a matched run
        # rule already overrides it above. An empty change set is absent too:
        # `no_match` over no files classifies nothing because there is nothing
        # to classify, which is a fact about the PR.
        unclassified = uncovered
        verdict = None
        skip_reason = None
        dry_run_recommended = False
        evaluation_status = EVALUATION_UNCLASSIFIED
        rationale = (
            f"{len(unclassified)} of {len(paths)} changed file(s) were read in "
            "full and no rule classified them. That is not evidence the PR is "
            "unrelated to agent capabilities — run the scan to decide."
        )

    # ``should_run`` is a friendlier alias of ``run_shipgate`` (identical
    # value); both are kept so 0.x consumers reading either field agree.
    next_action = _next_action(
        run=verdict,
        dry_run_recommended=dry_run_recommended,
        skip_reason=skip_reason,
        manifest_present=manifest_present,
        matched=matched,
        default_command=triggers.get(
            "default_command", "agents-shipgate verify --preview --json"
        ),
        rationale=rationale,
        evaluation_status=evaluation_status,
    )
    return {
        "schema_version": triggers.get("schema_version"),
        "input_status": input_status,
        "evaluation_status": evaluation_status,
        "should_run": verdict,
        "run_shipgate": verdict,
        "skip": None if verdict is None else not verdict,
        "force_run": has_force_run,
        "dry_run_recommended": dry_run_recommended,
        "skip_reason": skip_reason,
        "stop_conditions_fired": stop_fired,
        # Whether the stop actually decided the verdict. `stop_conditions_fired`
        # stays the raw block result so a consumer can still see it held; this
        # says whether anything contradicted it.
        "stop_conditions_terminal": stop_terminal,
        "stop_conditions_evaluated": stop_conditions_evaluated,
        "rationale": rationale,
        "matched_rules": matched,
        "changed_files": list(paths),
        # This stage's exclusion ledger, in the shape every other stage
        # emits (``schemas.exclusions``). Non-empty exactly when
        # ``evaluation_status`` is ``unclassified``: those are the changed
        # files the trigger removed from analysis while classifying none of
        # them, and recording them is what turns "nothing signals a
        # tool-surface change" into a claim someone can check.
        "surface_exclusions": _trigger_exclusion_ledger(unclassified).model_dump(
            mode="json"
        ),
        "diff_tokens": _matched_diff_tokens(triggers, diff_text),
        "next_action": next_action,
    }


#: Entries kept on the trigger's own ledger. Far below the shared cap on
#: purpose: when no rule matches, *every* changed file is unclassified, so
#: these rows enumerate a list the same payload already carries in full under
#: ``changed_files``. What the ledger adds is the accounting and the exact
#: ``total``, both of which survive truncation; a few hundred copies of one
#: identical sentence do not, and this result is embedded verbatim in
#: ``verifier.json`` and in the Codex boundary payload written to stdout.
_TRIGGER_LEDGER_ENTRY_LIMIT = 25


def _trigger_exclusion_ledger(paths: list[str]) -> SurfaceExclusionLedger:
    """The changed files this stage dropped without classifying them.

    Always ``route_blocked``: the trigger runs before any scan exists, so
    there is no evidence gap for it to point at, and the only accounting a
    stage with no decision can offer is to decline to publish one. That is
    exactly what happens — ``should_run`` is ``None`` and ``next_action``
    routes to the scan — so the record and the verdict say the same thing.
    """

    return SurfaceExclusionLedger.from_entries(
        [
            SurfaceExclusion(
                stage="trigger",
                subject=path,
                reason="unclassified_change",
                source_ref=path,
                detail=(
                    "No catalog rule classified this changed file, so nothing "
                    "about it routed a surface into a scan."
                ),
                accounting="route_blocked",
            )
            for path in paths
            if path
        ],
        limit=_TRIGGER_LEDGER_ENTRY_LIMIT,
    )


def _path_is_covered(pred: Any, path: str) -> bool:
    """Whether a matched rule's predicate tree classifies this one path.

    Only the *positive, per-file* predicates count. ``diff_contains`` matches a
    body, not a file; ``file_present``/``detect_returns``/``user_*`` are facts
    about the workspace; the ``none_match_*`` negatives assert an absence and
    so classify nothing. A rule that fired on any of those has said something
    about the change set as a whole and nothing about which files it read.

    ``any_of``/``all_of`` are both walked as OR, and that is the conservative
    direction rather than a shortcut: a leaf is consulted here only by asking
    whether it matches *this* path, so a leg that did not contribute to the
    rule firing cannot match it either.
    """

    if not isinstance(pred, dict):
        return False
    for key in ("any_of", "all_of"):
        if key in pred:
            return any(_path_is_covered(nested, path) for nested in pred[key])
    if "glob" in pred:
        return _glob_match(pred["glob"], path)
    if "every_file_matches" in pred:
        patterns = pred["every_file_matches"]
        if isinstance(patterns, str):
            patterns = [patterns]
        # Case-sensitive, exactly as the predicate itself matches.
        return any(_glob_match_exact(g, path) for g in patterns)
    if "boundary_adapter" in pred:
        return any(
            adapter.id == pred["boundary_adapter"]
            for adapter in boundary_adapters_for_path(path)
        )
    return False


def _unclassified_paths(
    triggers: dict[str, Any],
    matched: list[dict[str, Any]],
    paths: list[str],
) -> list[str]:
    """Changed files no matched rule classified.

    The skip that motivated this is not ``no_match``. A PR bumping
    ``requirements.txt`` beside an opaque ``.snap`` matches
    TRIGGER-FRAMEWORK-VERSION-BUMP, whose glob leg covers the manifest and says
    nothing about the sibling — and ``dry_run_only`` then published a confident
    advisory skip over a capability file nothing had read (PR #404 review).
    Coverage is therefore per path, not per change set.

    ``TRIGGER-DOCS-ONLY-NEGATIVE`` is unaffected by construction: its
    ``every_file_matches`` leg only fires when every changed file matches it,
    so a rule that classified the whole change set leaves nothing here. That is
    the difference between a negative rule and an absent one.
    """

    by_id = {
        rule["id"]: rule
        for rule in triggers.get("rules", [])
        if isinstance(rule, dict) and "id" in rule
    }
    predicates = [
        by_id[match["id"]].get("when")
        for match in matched
        # A rule carrying an action this build does not recognise contributes
        # no coverage. Its `when` clause fired, but the evaluator could not
        # act on it, so it decided nothing about the files it matched — and
        # counting them as classified would let a malformed catalog buy a
        # confident skip it never earned.
        if match.get("id") in by_id and match.get("action") in VALID_ACTIONS
    ]
    return [
        path
        for path in paths
        if path
        and not any(_path_is_covered(pred, path) for pred in predicates)
    ]


def _verdict_label(result: dict[str, Any]) -> str:
    """Render the run/skip/withheld verdict for human output."""

    status = result.get("evaluation_status")
    if status == EVALUATION_NOT_EVALUATED:
        return "NOT EVALUATED"
    if status == EVALUATION_UNCLASSIFIED:
        return "UNCLASSIFIED"
    return "RUN" if result.get("run_shipgate") else "SKIP"


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
    # Lazy import avoids a module cycle: the verifier orchestrator imports the
    # pure trigger evaluator, while this optional CLI-only path reuses the
    # verifier's audited Git transport.
    from agents_shipgate.cli.verify.git import (
        diff_revspec_context,
        working_tree_context,
    )

    root = cwd or Path.cwd()
    return (
        diff_revspec_context(root, revspec)
        if revspec
        else working_tree_context(root, reject_index_hidden=True)
    )


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
        except (FileNotFoundError, ConfigError) as exc:
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

    verdict = _verdict_label(result)
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
