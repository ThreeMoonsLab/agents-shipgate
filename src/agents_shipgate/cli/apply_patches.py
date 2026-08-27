"""``shipgate apply-patches`` — apply patches from a scan JSON report.

Per the v0.6 plan §4:
- Dry-run by default; ``--apply`` is required to mutate.
- Patches grouped by ``target_file``; each file is read once, SHA
  verified once, all patches in that group applied in memory, written
  once. (Two SHAs per patch would cause the second patch to fail after
  the first write — see plan A1.)
- Containment check (per C13): every ``target_file`` must resolve under
  ``report.manifest_dir``. Anything outside aborts with exit code 5.
- ``--confidence`` (default ``high``) and ``--kinds`` (default: all
  non-manual) filter the patches that get applied.
- YAML edits use ruamel.yaml round-trip preservation; JSON uses stdlib.

Exit codes:
- 0 — dry-run completed, or all patches applied.
- 2 — ``--from`` payload malformed.
- 4 — internal error.
- 5 — containment violation, or a ``declare_action`` patch that could not be
  written (the manifest answers the row differently, or has changed since the
  scan); nothing written.

``declare_action`` (report v0.39) is the one kind whose patches do not come
from ``findings[].patches``. It answers a declaration question published on
``release_decision.evidence_coverage.evidence_gaps[].next_action.patch``, it
is outside the default ``--kinds`` so no existing pipeline starts writing
declarations, and its refusals are the one file-level outcome this command
reports through the exit code — see ``_declare_action`` (#410 §D).
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError
from ruamel.yaml import YAML

from agents_shipgate.schemas.patches import (
    AppendPointerPatch,
    DeclareActionPatch,
    ManualPatch,
    Patch,
    RemovePointerPatch,
    SetPointerPatch,
)


def apply_patches(
    from_path: Path = typer.Option(
        ...,
        "--from",
        help="Path to a scan JSON report containing findings with patches.",
    ),
    confidence: str = typer.Option(
        "high",
        "--confidence",
        help="Minimum confidence level to include. One of low|medium|high.",
    ),
    kinds: str = typer.Option(
        "set_pointer,append_pointer,remove_pointer",
        "--kinds",
        help=(
            "Comma-separated patch kinds to include. ManualPatch is never "
            "applied; if you want it included for completeness pass "
            "manual explicitly. declare_action is deliberately outside the "
            "default set: it writes a new declaration into the manifest, so "
            "it is applied only when asked for by name."
        ),
    ),
    finding_ids: list[str] | None = typer.Option(
        None,
        "--finding-id",
        help=(
            "Apply patches only from this exact finding id. May be repeated. "
            "Every requested id must exist; omission keeps the legacy all-findings behavior."
        ),
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Actually mutate files. Default is dry-run.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit a structured summary on stdout.",
    ),
) -> None:
    """Apply patches grouped by target_file with SHA verification.

    Default is dry-run (prints diffs only). Use ``--apply`` to mutate.
    """
    confidence_levels = _confidence_set(confidence)
    kind_set = {k.strip() for k in kinds.split(",") if k.strip()}

    try:
        report = json.loads(from_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        message = f"Cannot parse JSON report at {from_path}: {exc}"
        typer.echo(message, err=True)
        _emit_malformed_patch_error(from_path, message)
        raise typer.Exit(2) from exc
    except OSError as exc:
        message = f"Cannot read JSON report at {from_path}: {exc}"
        typer.echo(message, err=True)
        _emit_malformed_patch_error(from_path, message)
        raise typer.Exit(2) from exc

    manifest_dir = report.get("manifest_dir")
    if not manifest_dir:
        message = (
            "Report does not include manifest_dir (pre-v0.6 report?). "
            "Cannot enforce the containment check; refusing to apply."
        )
        typer.echo(message, err=True)
        _emit_input_error(
            "other_error",
            message,
            next_action=(
                "Re-run scan with --suggest-patches to regenerate a current "
                "report before applying patches."
            ),
            next_actions=[
                {
                    "kind": "command",
                    "command": (
                        "agents-shipgate scan -c shipgate.yaml "
                        "--suggest-patches --format json"
                    ),
                    "path": None,
                    "why": (
                        "apply-patches requires report.manifest_dir so it "
                        "can prove every target_file stays inside the "
                        "manifest directory."
                    ),
                    "expects": (
                        "A current report.json with top-level manifest_dir "
                        "and findings[].patches[].target_file values."
                    ),
                }
            ],
        )
        raise typer.Exit(5)
    manifest_dir_resolved = Path(manifest_dir).resolve()

    findings = report.get("findings", [])
    requested_ids = set(finding_ids or [])
    available_ids = {
        str(finding.get("id"))
        for finding in findings
        if isinstance(finding, dict) and finding.get("id")
    }
    missing_ids = sorted(requested_ids - available_ids)
    if missing_ids:
        message = (
            "Requested finding id(s) are absent from the report: "
            + ", ".join(missing_ids)
        )
        typer.echo(message, err=True)
        _emit_malformed_patch_error(from_path, message)
        raise typer.Exit(2)

    raw_patches: list[dict[str, Any]] = []
    for finding in findings:
        if requested_ids and str(finding.get("id")) not in requested_ids:
            continue
        for patch in finding.get("patches") or []:
            raw_patches.append(patch)
    # Declaration patches hang off the evidence-gap rows, not off findings:
    # a declaration question is not a finding and never was (#410 §D). They
    # are skipped entirely under ``--finding-id``, which asks for the patches
    # of named findings and would otherwise silently widen to rows that have
    # no id to name.
    if not requested_ids:
        raw_patches.extend(_declaration_patches(report))

    # Coerce raw patches into typed Patch instances. A malformed payload
    # (missing required fields, unknown kind, etc.) maps to exit code 2
    # per the documented contract — not an uncaught Pydantic traceback
    # exiting 1.
    try:
        typed_patches = [
            _coerce_patch(p)
            for p in raw_patches
            if p.get("kind") in kind_set
            and (p.get("kind") == "manual" or p.get("confidence") in confidence_levels)
        ]
    except (ValidationError, typer.BadParameter) as exc:
        typer.echo(
            f"Malformed patch in report at {from_path}: {exc}",
            err=True,
        )
        import shlex as _shlex

        out_q = _shlex.quote(str(from_path.parent))
        rerun_command = (
            f"agents-shipgate scan -c shipgate.yaml --suggest-patches "
            f"--out {out_q}"
        )
        _emit_input_error(
            "malformed_patch",
            str(exc),
            next_action=rerun_command,
            next_actions=[
                {
                    "kind": "command",
                    "command": rerun_command,
                    "path": None,
                    "why": (
                        "Re-run scan with --suggest-patches to regenerate a "
                        "well-formed patch payload."
                    ),
                    "expects": (
                        f"{from_path} is rewritten with valid patches[] "
                        "entries."
                    ),
                }
            ],
        )
        raise typer.Exit(2) from exc
    typed_patches = [p for p in typed_patches if not isinstance(p, ManualPatch)]

    # Containment check (per C13). Every target must live under manifest_dir.
    #
    # A ``declare_action`` patch names its target *relative* to that directory,
    # so resolving it is what places it — the check below then cannot fail for
    # it except through a traversal spelled into the path, which is exactly
    # what it should still catch.
    violations: list[tuple[str, str]] = []
    for patch in typed_patches:
        spelled = _patch_target_spelling(patch)
        target = _resolve_target(patch, manifest_dir_resolved)
        try:
            target.relative_to(manifest_dir_resolved)
        except ValueError:
            violations.append((spelled, str(manifest_dir_resolved)))
    if violations:
        message = (
            "Containment violation: refusing to apply patches outside the "
            "manifest directory."
        )
        typer.echo(f"{message.rstrip('.')}:", err=True)
        for target, root in violations:
            typer.echo(f"  - {target} (not under {root})", err=True)
        _emit_input_error(
            "other_error",
            message,
            next_action=(
                f"Review {from_path}; every patch target_file must resolve "
                f"under {manifest_dir_resolved}."
            ),
            next_actions=[
                {
                    "kind": "review",
                    "command": None,
                    "path": str(from_path),
                    "why": (
                        "The report contains a machine-applicable patch whose "
                        "target_file escapes report.manifest_dir. "
                        "apply-patches refuses this to preserve the "
                        "containment boundary."
                    ),
                    "expects": (
                        "All non-manual patch target_file values resolve "
                        f"under {manifest_dir_resolved} before retrying."
                    ),
                }
            ],
        )
        raise typer.Exit(5)

    grouped: dict[str, list[Patch]] = defaultdict(list)
    for patch in typed_patches:
        grouped[str(_resolve_target(patch, manifest_dir_resolved))].append(patch)

    summary = _Summary()
    refused_declarations: list[tuple[str, str]] = []
    for target_file, patches in sorted(grouped.items()):
        outcome = _apply_one_file(Path(target_file), patches, apply=apply)
        summary.record(target_file, outcome)
        # ``skipped_drift`` counts too, and for the same reason ``error`` does:
        # a stale report writes nothing, and an agent that re-ran verify on a
        # silent exit 0 would come back with the identical route. Both outcomes
        # leave the file untouched, and both need the caller to do something
        # different before trying again.
        if outcome.status in {"error", "skipped_drift"} and any(
            isinstance(patch, DeclareActionPatch) for patch in patches
        ):
            refused_declarations.append((target_file, outcome.error or "refused"))

    if json_output:
        typer.echo(json.dumps(summary.as_dict(apply=apply), indent=2))
    else:
        summary.print(apply=apply)

    if refused_declarations:
        # The only outcome this command reports through the exit code beyond
        # the two input errors, and it is scoped to declarations on purpose.
        # An agent following ``next_action.kind: confirm_declarations`` runs
        # this command and then re-runs verify; if a refusal exited 0 it would
        # re-run against an unchanged manifest, get the identical route back,
        # and loop. Every other kind keeps the exit status it has always had.
        message = (
            "Did not write "
            + ("a declaration" if len(refused_declarations) == 1 else "declarations")
            + " into the manifest; nothing was changed."
        )
        typer.echo(message, err=True)
        for target, detail in refused_declarations:
            typer.echo(f"  - {target}: {detail}", err=True)
        _emit_input_error(
            "other_error",
            message,
            next_action=(
                "Answer the conflicting declaration by hand in the manifest, "
                "or re-run the scan if the manifest changed since it ran."
            ),
            next_actions=[
                {
                    "kind": "review",
                    "command": None,
                    "path": refused_declarations[0][0],
                    "why": (
                        "Either the manifest already answers one of these action "
                        "rows differently or names the same tool twice — a "
                        "derived proposal never replaces a reviewed answer — or "
                        "it has changed since the scan that produced this report."
                    ),
                    "expects": (
                        "The named action_surface.actions row carries the "
                        "effect its reviewer intends, then rerun verification."
                    ),
                }
            ],
        )
        raise typer.Exit(5)


# --- Internals --------------------------------------------------------------


def _emit_input_error(kind: str, message: str, **fields: object) -> None:
    """Emit this command's agent-mode errors through the shared emitter.

    This used to be a second, parallel implementation that reproduced the
    payload by hand. Two things followed from that, and both were invisible
    until something looked: the line never carried the ``command`` field every
    other agent-mode error carries, and — because the recovery routes here are
    built as plain dicts rather than
    :class:`~agents_shipgate.schemas.diagnostics.NextAction` objects — they
    silently opted out of the invocation policy, advertising a console script
    to a caller that had none (#322).

    ``fields`` may carry ``next_action`` (legacy single string) and
    ``next_actions`` (ranked list of action dicts); the shared emitter
    normalizes both.
    """

    from agents_shipgate.cli.agent_mode import emit_agent_mode_error

    emit_agent_mode_error(kind, message=message, **fields)


def _emit_malformed_patch_error(from_path: Path, message: str) -> None:
    _emit_input_error(
        "malformed_patch",
        message,
        next_action=(
            f"Verify {from_path} is a readable report generated by "
            "agents-shipgate scan --suggest-patches."
        ),
        next_actions=[
            {
                "kind": "review",
                "command": None,
                "path": str(from_path),
                "why": (
                    "apply-patches could not load the report payload before "
                    "validating patches."
                ),
                "expects": (
                    "A readable JSON report with findings[].patches[] entries "
                    "from a scan run with --suggest-patches."
                ),
            }
        ],
    )


def _resolve_target(patch: Patch, manifest_dir: Path) -> Path:
    """The file a patch writes, as an absolute path on this machine.

    Two spellings meet here and only here. The pointer patches carry an
    absolute ``target_file`` (v0.6, C13); a ``declare_action`` patch carries a
    ``target_path`` relative to ``manifest_dir``, because that row is embedded
    by artifacts that leave this machine. Resolving both in one place is what
    keeps the containment check and the write grouping reading the same answer.
    """

    if isinstance(patch, DeclareActionPatch):
        return (manifest_dir / patch.target_path).resolve()
    return Path(patch.target_file).resolve()


def _patch_target_spelling(patch: Patch) -> str:
    """The target as the patch itself spells it, for a message a reader can match."""

    if isinstance(patch, DeclareActionPatch):
        return patch.target_path
    return patch.target_file


def _declaration_patches(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Every ``declare_action`` patch the report published on a gap row.

    Read defensively rather than validated: this function walks a payload the
    caller supplied, and a report written by an older build simply has none of
    these keys. Anything that is not a mapping is skipped, and the patches it
    does find go through the same ``_coerce_patch`` / confidence / kind filters
    as a finding's — nothing here is a second admission path.
    """

    decision = report.get("release_decision")
    if not isinstance(decision, dict):
        return []
    coverage = decision.get("evidence_coverage")
    if not isinstance(coverage, dict):
        return []
    gaps = coverage.get("evidence_gaps")
    if not isinstance(gaps, list):
        return []
    out: list[dict[str, Any]] = []
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        action = gap.get("next_action")
        if not isinstance(action, dict):
            continue
        patch = action.get("patch")
        if isinstance(patch, dict):
            out.append(patch)
    return out


def _confidence_set(min_level: str) -> set[str]:
    order = ["low", "medium", "high"]
    if min_level not in order:
        raise typer.BadParameter(f"--confidence must be one of {order}")
    threshold = order.index(min_level)
    return set(order[threshold:])


def _coerce_patch(payload: dict[str, Any]) -> Patch:
    kind = payload.get("kind")
    if kind == "set_pointer":
        return SetPointerPatch.model_validate(payload)
    if kind == "append_pointer":
        return AppendPointerPatch.model_validate(payload)
    if kind == "remove_pointer":
        return RemovePointerPatch.model_validate(payload)
    if kind == "declare_action":
        return DeclareActionPatch.model_validate(payload)
    if kind == "manual":
        return ManualPatch.model_validate(payload)
    raise typer.BadParameter(f"Unknown patch kind: {kind}")


@dataclass
class _FileOutcome:
    status: str  # "applied" | "dry_run" | "skipped_drift" | "error"
    patches_in_group: int
    diff: str | None = None
    error: str | None = None


@dataclass
class _Summary:
    files: dict[str, _FileOutcome] = field(default_factory=dict)

    def record(self, file: str, outcome: _FileOutcome) -> None:
        self.files[file] = outcome

    def print(self, *, apply: bool) -> None:
        if not self.files:
            typer.echo("No patches matched the filters.")
            return
        for file, outcome in self.files.items():
            typer.echo(f"=== {file} ({outcome.status}) ===")
            if outcome.diff:
                typer.echo(outcome.diff)
            if outcome.error:
                typer.echo(f"  error: {outcome.error}", err=True)
        if not apply:
            typer.echo("")
            typer.echo("Dry-run only. Pass --apply to mutate files.")

    def as_dict(self, *, apply: bool) -> dict[str, Any]:
        return {
            "applied": apply,
            "files": {
                file: {
                    "status": outcome.status,
                    "patches": outcome.patches_in_group,
                    "diff": outcome.diff,
                    "error": outcome.error,
                }
                for file, outcome in self.files.items()
            },
        }


def _apply_one_file(
    path: Path, patches: list[Patch], *, apply: bool
) -> _FileOutcome:
    """Read once, verify SHA once, apply all in memory, write once."""
    if not path.exists():
        return _FileOutcome(
            status="error",
            patches_in_group=len(patches),
            error=f"target file does not exist: {path}",
        )
    # Hashed as **bytes**, because that is what the generator hashed. Reading
    # as text normalizes CRLF to LF, so an untouched CRLF manifest produced a
    # digest the patch could never match and reported ``skipped_drift`` for
    # ever — and, once a declaration refusal became an exit code, an
    # unbreakable loop for the agent following the route.
    raw = path.read_bytes()
    current_sha = hashlib.sha256(raw).hexdigest()
    original_text = raw.decode("utf-8")
    # The style the file is written in, preserved across the round trip: the
    # YAML and JSON writers below both emit "\n", so a CRLF manifest would
    # otherwise come back with every line ending rewritten and a diff that is
    # entirely newline noise.
    newline = "\r\n" if b"\r\n" in raw else "\n"

    expected_shas = {p.target_sha256 for p in patches}
    if expected_shas != {current_sha}:
        return _FileOutcome(
            status="skipped_drift",
            patches_in_group=len(patches),
            error=(
                "file SHA does not match patches' target_sha256 (file changed "
                "since scan); re-run scan and re-apply."
            ),
        )

    suffix = path.suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            new_text = _apply_yaml(original_text, patches)
        elif suffix == ".json":
            new_text = _apply_json(original_text, patches)
        else:
            new_text = None
    except DeclarationConflict as exc:
        # Refused, not crashed, and nothing is written: the whole group is
        # applied in memory before the single write, so an abort here leaves
        # the file exactly as it was. Only this one exception is caught —
        # every other failure keeps its current loudness rather than being
        # downgraded to a summary row.
        return _FileOutcome(
            status="error",
            patches_in_group=len(patches),
            error=str(exc),
        )
    if new_text is None:
        return _FileOutcome(
            status="error",
            patches_in_group=len(patches),
            error=f"unsupported target format for {path.suffix}",
        )

    # Converted before it is compared, so a patch that changes nothing stays a
    # no-op on a CRLF file instead of rewriting every line ending.
    new_text = _with_newlines(new_text, newline)
    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
    )

    if apply and original_text != new_text:
        path.write_bytes(new_text.encode("utf-8"))
        return _FileOutcome(status="applied", patches_in_group=len(patches), diff=diff)

    return _FileOutcome(
        status="applied" if apply else "dry_run",
        patches_in_group=len(patches),
        diff=diff,
    )


def _with_newlines(text: str, newline: str) -> str:
    """Re-emit ``text`` in the newline style the file was written in."""

    if newline == "\n":
        return text
    return text.replace("\r\n", "\n").replace("\n", newline)


def _apply_yaml(text: str, patches: list[Patch]) -> str:
    yaml = YAML(typ="rt")  # round-trip preserves comments + key order
    yaml.preserve_quotes = True
    yaml.width = 4096
    # Round-tripping preserves comments and key order but not per-node
    # indentation — ruamel re-emits every sequence at one global setting. Left
    # at the default, writing one declaration also re-indented every unrelated
    # list in the manifest, and the PR diff of a trust-root edit was mostly
    # whitespace. These are the values ``init`` writes (``  - id:``), so a
    # manifest Shipgate generated round-trips byte-identical apart from the
    # patch itself, which is what makes "the reviewer reads exceptions" true.
    yaml.indent(mapping=2, sequence=4, offset=2)
    data = yaml.load(text) or {}
    for patch in _ordered_for_apply(patches):
        _apply_patch_to_data(data, patch)
    import io

    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def _apply_json(text: str, patches: list[Patch]) -> str:
    data = json.loads(text)
    for patch in _ordered_for_apply(patches):
        _apply_patch_to_data(data, patch)
    return json.dumps(data, indent=2) + "\n"


def _ordered_for_apply(patches: list[Patch]) -> list[Patch]:
    """Order patches so list-mutating removes don't invalidate each other.

    Two removes against the same YAML list (e.g. /policies/.../0 and
    /policies/.../1) crash or silently delete the wrong element when
    applied in report order: the first delete shifts subsequent indexes.

    Apply sets and appends first (they don't shift indexes), then
    removes — sorted so deeper pointers fire before shallower ones
    (children before parents) and within a shared parent list, higher
    indexes fire before lower indexes.
    """
    sets_and_appends: list[Patch] = []
    removes: list[RemovePointerPatch] = []
    others: list[Patch] = []
    for patch in patches:
        if isinstance(patch, RemovePointerPatch):
            removes.append(patch)
        elif isinstance(patch, (SetPointerPatch, AppendPointerPatch, DeclareActionPatch)):
            # A declaration either fills a blank on an existing row or appends
            # one; neither shifts an index a pending remove already named.
            sets_and_appends.append(patch)
        else:
            others.append(patch)
    return sets_and_appends + sorted(removes, key=_remove_sort_key) + others


def _remove_sort_key(patch: RemovePointerPatch) -> tuple:
    """Sort key that puts deeper pointers first, then within the same
    parent puts higher list indexes first."""
    tokens = _split_pointer(patch.pointer)
    parent = tuple(tokens[:-1])
    leaf = tokens[-1] if tokens else ""
    try:
        # Numeric leaf: sort descending (priority 0).
        leaf_key: tuple = (0, -int(leaf))
    except ValueError:
        # Dict-key leaf: order doesn't matter for correctness.
        leaf_key = (1, leaf)
    # -depth so deeper pointers (longer token lists) sort first.
    return (-len(tokens), parent, leaf_key)


def _apply_patch_to_data(root: Any, patch: Patch) -> None:
    if isinstance(patch, SetPointerPatch):
        _set_pointer(root, patch.pointer, patch.value)
    elif isinstance(patch, AppendPointerPatch):
        _append_pointer(root, patch.pointer, patch.value)
    elif isinstance(patch, RemovePointerPatch):
        _remove_pointer(root, patch.pointer)
    elif isinstance(patch, DeclareActionPatch):
        _declare_action(root, patch)
    elif isinstance(patch, ManualPatch):
        # No-op (filtered out earlier; defensive).
        return


class DeclarationConflict(ValueError):
    """A declaration patch would have overwritten or guessed at an answer.

    Its own exception type because the recovery is different from every other
    patch failure: nothing here is stale or malformed, the manifest simply
    already says something the proposal is not allowed to change. The whole
    file group fails so nothing partial is written, and the message names the
    row and the field a human has to look at.
    """


def _declare_action(root: Any, patch: DeclareActionPatch) -> None:
    """Write one declaration into ``action_surface.actions``, or refuse.

    Three outcomes, and the refusals are the point:

    * **No row names this tool** — append ``{**selector, **declaration}``.
    * **Exactly one row names it** — write only the fields it leaves silent.
      A field it already answers *differently* is a human's answer, and an
      evidence-derived proposal may never replace one (#410 §D). Refuse.
    * **More than one row names it** — the manifest disambiguates two
      same-named actions by ``tool_id``, and picking one here would be a guess
      about which action the evidence was read from. Refuse.

    Matching is on ``tool`` alone, then checked. Matching on the whole selector
    instead would miss a row that declares ``tool:`` without ``tool_id:`` and
    append a second row for the same action — two selectors resolving to one
    tool, which the manifest rejects as a duplicate on the next load. Missing
    the row is the failure mode that writes something wrong; finding it and
    disagreeing is the one that stops.
    """

    surface = root.get("action_surface")
    if surface is None:
        surface = {}
        root["action_surface"] = surface
    actions = surface.get("actions")
    if actions is None:
        actions = []
        surface["actions"] = actions
    if not isinstance(actions, list):
        raise DeclarationConflict(
            "action_surface.actions must be a list of declarations; "
            f"found {type(actions).__name__}"
        )

    tool = patch.selector.get("tool")
    named = [row for row in actions if isinstance(row, dict) and row.get("tool") == tool]
    # A row whose qualifiers *disagree* with this selector is a different
    # action that happens to share a display name — two providers exporting
    # ``send_email`` is a supported shape, and the manifest tells them apart by
    # ``tool_id``/``source_id``. Such a row is skipped, not refused: refusing
    # made a batch of valid patches unexecutable, because the first one
    # appended a row the second then read as its own mismatched match.
    matches = [row for row in named if not _selector_conflicts(row, patch.selector)]
    if not matches:
        actions.append({**patch.selector, **patch.declaration})
        return
    if len(matches) > 1:
        raise DeclarationConflict(
            f"{len(matches)} action_surface.actions rows are compatible with tool "
            f"{tool!r}; refusing to guess which one this declaration answers for."
        )
    row = matches[0]
    for key, value in patch.declaration.items():
        existing = row.get(key)
        if key in row and existing is not None and existing != value:
            raise DeclarationConflict(
                f"action_surface.actions row for tool {tool!r} already "
                f"declares {key}: {existing!r}; a derived proposal never "
                "replaces a reviewed answer."
            )
        row[key] = value


def _selector_conflicts(row: dict, selector: dict) -> bool:
    """Does this row name a *different* action than the selector does?

    Only keys both spell can disagree. A row that declares ``tool:`` alone is
    compatible with a qualified selector — that is the common shape, a human
    wrote the row and the scan knows more about it than they typed — while a
    row carrying a different ``tool_id`` is a different capability with the
    same display name, and writing into it would answer the wrong question.

    ``tool`` itself is not consulted: the caller has already grouped on it.
    """

    return any(
        key in row and row[key] != value
        for key, value in selector.items()
        if key != "tool"
    )


def _split_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {pointer!r}")
    if pointer == "/":
        return []
    return [
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer[1:].split("/")
    ]


def _navigate_parent(root: Any, tokens: list[str]) -> tuple[Any, str]:
    """Walk to the parent of the leaf; return (parent, leaf_token)."""
    parent = root
    for token in tokens[:-1]:
        if isinstance(parent, list):
            parent = parent[int(token)]
        else:
            parent = parent[token]
    return parent, tokens[-1]


def _set_pointer(root: Any, pointer: str, value: Any) -> None:
    tokens = _split_pointer(pointer)
    if not tokens:
        raise ValueError("set_pointer cannot target the document root")
    # Walk + create intermediate dicts if needed (mimics RFC 6902 'add' for
    # missing parents on YAML manifests where set is the natural op).
    cursor = root
    for token in tokens[:-1]:
        if isinstance(cursor, list):
            cursor = cursor[int(token)]
        else:
            if token not in cursor:
                cursor[token] = {}
            cursor = cursor[token]
    leaf = tokens[-1]
    if isinstance(cursor, list):
        cursor[int(leaf)] = value
    else:
        cursor[leaf] = value


def _append_pointer(root: Any, pointer: str, value: Any) -> None:
    tokens = _split_pointer(pointer)
    if not tokens:
        raise ValueError("append_pointer cannot target the document root")
    cursor = root
    for token in tokens[:-1]:
        if isinstance(cursor, list):
            cursor = cursor[int(token)]
        else:
            if token not in cursor:
                cursor[token] = {}
            cursor = cursor[token]
    leaf = tokens[-1]
    if isinstance(cursor, list):
        cursor.append(value)
        return
    target = cursor.get(leaf)
    if target is None:
        cursor[leaf] = [value]
    elif isinstance(target, list):
        target.append(value)
    else:
        raise ValueError(
            f"append_pointer target must be a list or absent; got {type(target).__name__}"
        )


def _remove_pointer(root: Any, pointer: str) -> None:
    tokens = _split_pointer(pointer)
    if not tokens:
        raise ValueError("remove_pointer cannot target the document root")
    parent, leaf = _navigate_parent(root, tokens)
    if isinstance(parent, list):
        del parent[int(leaf)]
    else:
        if leaf in parent:
            del parent[leaf]
