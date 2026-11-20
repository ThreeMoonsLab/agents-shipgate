"""Build a GitHub Check Run payload from Shipgate verify artifacts.

Invoked by ``action.yml`` when ``check_run: true``: reads
``verifier.json`` / ``report.sarif`` / ``pr-comment.md`` from
``$OUTPUT_DIR`` and writes ``check-run-payload.json`` next to them. The
actual Checks API call happens in a ``github-script`` step (same token
path as the PR comment step); this script stays pure and testable.

Mapping contract:

- ``merge_verdict`` + ``check_run_policy`` → check conclusion. The default
  ``advisory`` policy preserves the historical mapping
  (``mergeable`` → ``success``, ``blocked`` → ``failure``, everything else
  → ``neutral``). ``blocked-fails`` maps ``blocked`` and ``unknown`` to
  ``failure`` while leaving human-routed verdicts neutral. ``require-mergeable``
  is the strict branch-protection policy: only
  ``can_merge_without_human=true`` succeeds. Under either of those two, a run
  that states no merge verdict — no readable ``verifier.json``, or one without
  ``merge_verdict`` — fails, and the summary says why. Any other policy value
  is an error, never ``advisory``, and so is either strict policy with
  ``verify_mode: scan``, which writes no ``verifier.json`` (#1032). The conclusion mirrors — never replaces — the one
  decision engine: ``report.json.release_decision.decision`` stays the gate.
- The verdict is read only through the outputs step's own validation
  (``github_action_outputs.validate_evidence``), bound to this invocation: the
  CLI completed, and the receipt names the evaluated repository and head. A
  directory it refuses states no merge verdict, so the check is never
  ``success`` and its summary says why. ``success`` also needs a receipt, and
  that receipt must be the one the outputs step validated and published as
  ``receipt_id`` in this job (#1165).
- PR projection items → up to ``MAX_ANNOTATIONS`` line-level annotations
  (Checks API caps one request at 50). SARIF remains the full finding export;
  Check Runs intentionally annotate only merge-relevant PR items.
- ``pr-comment.md`` → check summary (truncated to the API limit).
- The payload is written as a new file, never through whatever occupied its
  path, and its SHA-256 goes to ``GITHUB_OUTPUT`` as ``payload_sha256``. The
  step that creates the check posts only a payload with that digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from agents_shipgate.report.pr_projection import (
    item_to_check_run_annotation,
    select_pr_items,
)

if __package__:  # Imported from the repository (the tests).
    from scripts.github_action_outputs import ActionInvocation, ValidatedEvidence, validate_evidence
else:  # Run by path from action.yml, with this directory first on sys.path.
    from github_action_outputs import ActionInvocation, ValidatedEvidence, validate_evidence

MAX_ANNOTATIONS = 50
# Checks API hard limit is 65535 chars for output.summary; keep headroom.
MAX_SUMMARY_CHARS = 60000
PAYLOAD_FILENAME = "check-run-payload.json"
DEFAULT_CHECK_NAME = "Agents Shipgate"
DEFAULT_CHECK_RUN_POLICY = "advisory"
#: The spellings `action.yml`'s `check_run_policy` input and the docs give.
#: Accepted exactly, surrounding whitespace and letter case aside, and nothing
#: else: an unknown value used to fall back to ``advisory``, which turned the
#: underscore typo ``require_mergeable`` into a passing ``neutral`` check for a
#: verdict that needs a human (#1032).
CHECK_RUN_POLICIES = frozenset({"advisory", "blocked-fails", "require-mergeable"})
#: The policies under which a run that states no merge verdict fails.
STRICT_CHECK_RUN_POLICIES = frozenset({"blocked-fails", "require-mergeable"})


class CheckRunPolicyError(ValueError):
    """``check_run_policy`` names no documented policy."""


_CONCLUSIONS = {
    "mergeable": "success",
    "blocked": "failure",
}

_SARIF_LEVELS = {
    "error": "failure",
    "warning": "warning",
}


def conclusion_for(
    merge_verdict: object,
    *,
    policy: str = DEFAULT_CHECK_RUN_POLICY,
    can_merge_without_human: object = None,
) -> str:
    """Map a merge verdict onto a Checks API conclusion.

    The default policy is advisory for backward compatibility. Strict branch
    protection should use ``require-mergeable``.
    """
    normalized_policy = normalize_check_run_policy(policy)
    if normalized_policy == "require-mergeable":
        return "success" if _truthy(can_merge_without_human) else "failure"
    if normalized_policy == "blocked-fails":
        if not isinstance(merge_verdict, str) or not merge_verdict.strip():
            # No verdict at all is the `unknown` this policy exists to fail.
            return "failure"
        if merge_verdict == "mergeable":
            return "success"
        if merge_verdict in {"blocked", "unknown"}:
            return "failure"
        return "neutral"
    if not isinstance(merge_verdict, str):
        return "neutral"
    return _CONCLUSIONS.get(merge_verdict, "neutral")


def normalize_check_run_policy(policy: object) -> str:
    """The documented policy ``policy`` names; :class:`CheckRunPolicyError` otherwise.

    An empty value is the input's default, ``advisory``. Anything else must be
    one of :data:`CHECK_RUN_POLICIES`.
    """

    if policy is None:
        return DEFAULT_CHECK_RUN_POLICY
    if not isinstance(policy, str):
        raise CheckRunPolicyError(f"check_run_policy must be a string, not {policy!r}.")
    normalized = policy.strip().lower()
    if not normalized:
        return DEFAULT_CHECK_RUN_POLICY
    if normalized in CHECK_RUN_POLICIES:
        return normalized
    hyphenated = normalized.replace("_", "-")
    hint = f" Did you mean {hyphenated!r}?" if hyphenated in CHECK_RUN_POLICIES else ""
    raise CheckRunPolicyError(
        f"Unknown check_run_policy {policy!r}. Supported values: "
        f"{', '.join(sorted(CHECK_RUN_POLICIES))}.{hint}"
    )


def check_policy_for_mode(policy: str, verify_mode: object) -> None:
    """Refuse a strict policy under ``verify_mode: scan``, which writes no verdict.

    `blocked-fails` and `require-mergeable` are defined on the merge verdict
    and `can_merge_without_human` in `verifier.json`. A scan writes
    `report.json` alone, so under either policy every scan run would conclude
    `failure`, a passed one included. Mapping the report's release decision
    onto a verdict here would be a second derivation of what `verify` decides,
    so the combination is refused instead (#1032).
    """

    mode = verify_mode.strip() if isinstance(verify_mode, str) else ""
    if mode == "scan" and policy in STRICT_CHECK_RUN_POLICIES:
        raise CheckRunPolicyError(
            f"check_run_policy {policy} reads the merge verdict in verifier.json, which "
            "verify_mode scan does not write, so every run would fail its check. Use "
            "verify_mode verify (the default), or check_run_policy advisory with scan."
        )


def title_for(merge_verdict: object, *, blocker_count: int = 0) -> str:
    verdict = merge_verdict if isinstance(merge_verdict, str) and merge_verdict else "unknown"
    if verdict == "blocked" and blocker_count:
        noun = "blocker" if blocker_count == 1 else "blockers"
        return f"merge_verdict: blocked ({blocker_count} {noun})"
    return f"merge_verdict: {verdict}"


def annotations_from_pr_projection(
    report: dict[str, Any] | None,
    verifier: dict[str, Any] | None,
    *,
    limit: int = MAX_ANNOTATIONS,
) -> list[dict[str, Any]]:
    annotations: list[dict[str, Any]] = []
    for item in select_pr_items(report, verifier, limit=max(0, limit)):
        if not item.source_path:
            continue
        annotations.append(item_to_check_run_annotation(item))
    return annotations


def annotations_from_sarif(sarif: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Project SARIF results onto Checks API annotations.

    Compatibility fallback only. Normal check runs use
    :func:`annotations_from_pr_projection` so job annotations and Check Run
    annotations show the same merge-relevant items.
    """
    if not isinstance(sarif, dict):
        return []
    annotations: list[dict[str, Any]] = []
    for run in sarif.get("runs") or []:
        for result in run.get("results") or []:
            if len(annotations) >= MAX_ANNOTATIONS:
                return annotations
            location = _first_location(result)
            if location is None:
                continue
            path, start_line = location
            message = ((result.get("message") or {}).get("text") or "").strip()
            if not message:
                continue
            annotations.append(
                {
                    "path": path,
                    "start_line": start_line,
                    "end_line": start_line,
                    "annotation_level": _SARIF_LEVELS.get(result.get("level"), "notice"),
                    "message": message[:1000],
                    "title": str(result.get("ruleId") or "agents-shipgate"),
                }
            )
    return annotations


def _first_location(result: dict[str, Any]) -> tuple[str, int] | None:
    for location in result.get("locations") or []:
        physical = location.get("physicalLocation") or {}
        artifact = physical.get("artifactLocation") or {}
        uri = artifact.get("uri")
        if not isinstance(uri, str) or not uri:
            continue
        region = physical.get("region") or {}
        start_line = region.get("startLine")
        if not isinstance(start_line, int) or start_line < 1:
            start_line = 1
        # Older Shipgate SARIF carries literal paths, including any percent
        # characters in their filenames. Decode only a marked projection.
        properties = location.get("properties")
        if isinstance(properties, dict) and properties.get("shipgateArtifactUriEncoding") == "percent":
            uri = unquote(uri)
        return uri, start_line
    return None


def build_check_run_payload(
    *,
    verifier: dict[str, Any] | None,
    report: dict[str, Any] | None = None,
    sarif: dict[str, Any] | None = None,
    summary_markdown: str,
    name: str = DEFAULT_CHECK_NAME,
    check_run_policy: str = DEFAULT_CHECK_RUN_POLICY,
) -> dict[str, Any]:
    policy = normalize_check_run_policy(check_run_policy)
    verifier_missing = verifier is None
    verifier = verifier or {}
    merge_verdict = verifier.get("merge_verdict")
    release_decision = verifier.get("release_decision") or {}
    blockers = release_decision.get("blockers") or []
    annotations = annotations_from_pr_projection(report, verifier)
    if not annotations and report is None:
        annotations = annotations_from_sarif(sarif)
    total_results = _sarif_result_count(sarif)
    conclusion = conclusion_for(
        merge_verdict,
        policy=policy,
        can_merge_without_human=verifier.get("can_merge_without_human"),
    )
    summary = summary_markdown.strip() or "No Shipgate summary was produced."
    no_verdict = not isinstance(merge_verdict, str) or not merge_verdict.strip()
    if conclusion == "failure" and policy in STRICT_CHECK_RUN_POLICIES and no_verdict:
        reason = (
            "No readable `verifier.json` was produced"
            if verifier_missing
            else "`verifier.json` carries no `merge_verdict`"
        )
        summary = (
            f"_{reason}, so this run states no merge verdict. The `{policy}` "
            f"check-run policy fails the check._\n\n{summary}"
        )
    if total_results > len(annotations):
        summary += (
            f"\n\n_{len(annotations)} of {total_results} findings shown as "
            "merge-relevant line annotations; see the uploaded report "
            "artifact for the full list._"
        )
    return {
        "name": name,
        "conclusion": conclusion,
        "output": {
            "title": title_for(merge_verdict, blocker_count=len(blockers)),
            "summary": summary[:MAX_SUMMARY_CHARS],
            "annotations": annotations,
        },
    }


#: The most of a refusal's own text a check summary carries.
MAX_REFUSAL_CHARS = 400


def refused_check_run_payload(
    *,
    reason: str,
    name: str = DEFAULT_CHECK_NAME,
    check_run_policy: str = DEFAULT_CHECK_RUN_POLICY,
) -> dict[str, Any]:
    """The check for a run whose merge verdict could not be admitted.

    Concluded as a run that states no verdict: ``failure`` under the strict
    policies, ``neutral`` under ``advisory``, never ``success``. Nothing from
    the refused directory is shown, so no summary or annotation it carries can
    read as this run's.
    """

    policy = normalize_check_run_policy(check_run_policy)
    conclusion = conclusion_for(None, policy=policy)
    detail = " ".join(reason.replace("`", "'").split())
    if len(detail) > MAX_REFUSAL_CHARS:
        detail = detail[: MAX_REFUSAL_CHARS - 1].rstrip() + "…"
    summary = (
        f"_This run's verification evidence was not admitted, so it states no merge "
        f"verdict and the `{policy}` check-run policy concludes `{conclusion}`._\n\n"
        f"Reason: `{detail}`\n\n"
        "The job's log and the Agents Shipgate outputs step name the cause. Re-run the "
        "workflow once it is fixed; nothing from that directory is shown here."
    )
    return {
        "name": name,
        "conclusion": conclusion,
        "output": {
            "title": "merge_verdict: unknown (evidence not admitted)",
            "summary": summary[:MAX_SUMMARY_CHARS],
            "annotations": [],
        },
    }


def check_run_payload_from_evidence(
    output_dir: Path,
    *,
    invocation: ActionInvocation,
    validated_receipt_id: str,
    name: str = DEFAULT_CHECK_NAME,
    check_run_policy: str = DEFAULT_CHECK_RUN_POLICY,
) -> dict[str, Any]:
    """The Check Run payload for this invocation's output directory.

    The directory goes through :func:`validate_evidence`, the outputs step's
    own validation, bound to ``invocation``. Anything it refuses gives
    :func:`refused_check_run_payload`. A verdict it admits is mapped as before
    (blocked and review-required outcomes keep their policy mapping), except
    that ``success`` is concluded only from a terminal receipt that is the one
    the outputs step validated in this job (``validated_receipt_id``) and whose
    own outcome maps to ``success`` too.
    """

    policy = normalize_check_run_policy(check_run_policy)
    try:
        evidence = validate_evidence(output_dir, invocation=invocation)
    except Exception as exc:  # noqa: BLE001 - whatever stops validation refuses the directory.
        return refused_check_run_payload(
            reason=f"{type(exc).__name__}: {exc}", name=name, check_run_policy=policy
        )
    verifier = dict(evidence.verifier) if evidence.verifier else None
    if verifier is not None:
        # The validated control projection, not the raw field.
        verifier["can_merge_without_human"] = evidence.can_merge_without_human
    # SARIF annotates only a run with no projection source of its own: the
    # scan route. Beside a verifier that names no report it is another run's.
    sarif = _load_json(output_dir / "report.sarif") if verifier is None else None
    payload = build_check_run_payload(
        verifier=verifier,
        report=evidence.report,
        sarif=sarif,
        summary_markdown=_read_text(output_dir / "pr-comment.md"),
        name=name,
        check_run_policy=policy,
    )
    if payload["conclusion"] == "success":
        withheld = _success_withheld(evidence, validated_receipt_id, policy)
        if withheld is not None:
            return refused_check_run_payload(reason=withheld, name=name, check_run_policy=policy)
    return payload


def _success_withheld(
    evidence: ValidatedEvidence, validated_receipt_id: str, policy: str
) -> str | None:
    receipt = evidence.receipt
    if receipt is None:
        return "no verification receipt binds this verdict, so it cannot pass the check"
    if not validated_receipt_id or validated_receipt_id != receipt.receipt_id:
        return (
            "the Agents Shipgate outputs step did not validate this receipt in this job, "
            "so its verdict cannot pass the check"
        )
    if (
        conclusion_for(
            receipt.merge_verdict,
            policy=policy,
            can_merge_without_human=receipt.can_merge_without_human,
        )
        != "success"
    ):
        return "the verification receipt's own outcome does not pass this check-run policy"
    return None


def write_payload(path: Path, payload: dict[str, Any]) -> str:
    """Write ``payload`` as a new file at ``path`` and return its SHA-256.

    Whatever was there is removed first, a file or a link, and never written
    through: a payload left by an earlier run or shipped in the checkout cannot
    stand in for this one, and a link cannot point the write elsewhere. A
    directory there is an error.
    """

    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if stat.S_ISDIR(existing.st_mode):
            raise IsADirectoryError(f"{path} is a directory, not a check-run payload")
        path.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _sarif_result_count(sarif: dict[str, Any] | None) -> int:
    if not isinstance(sarif, dict):
        return 0
    return sum(len(run.get("results") or []) for run in sarif.get("runs") or [])


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Agents Shipgate check-run payload.")
    parser.add_argument(
        "--check-policy",
        action="store_true",
        help="only validate CHECK_RUN_POLICY against VERIFY_MODE; read and write no artifacts",
    )
    args = parser.parse_args(argv)
    try:
        check_run_policy = normalize_check_run_policy(os.environ.get("CHECK_RUN_POLICY"))
        check_policy_for_mode(check_run_policy, os.environ.get("VERIFY_MODE"))
    except CheckRunPolicyError as exc:
        print(f"Agents Shipgate check run refused: {exc}", file=sys.stderr)
        return 2
    if args.check_policy:
        return 0
    output_dir = Path(os.environ.get("OUTPUT_DIR") or "agents-shipgate-reports")
    name = os.environ.get("CHECK_RUN_NAME") or DEFAULT_CHECK_NAME
    payload = check_run_payload_from_evidence(
        output_dir,
        invocation=ActionInvocation.from_env(),
        # The outputs step publishes `receipt_id` only after it validated this
        # directory: empty when it refused it, failed, or found no receipt.
        validated_receipt_id=(os.environ.get("VALIDATED_RECEIPT_ID") or "").strip(),
        name=name,
        check_run_policy=check_run_policy,
    )
    out_path = output_dir / PAYLOAD_FILENAME
    digest = write_payload(out_path, payload)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"payload_sha256={digest}\n")
    print(f"Wrote {out_path} ({payload['conclusion']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
