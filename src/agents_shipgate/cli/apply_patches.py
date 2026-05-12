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
- 5 — containment violation; refused to apply.
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

from agents_shipgate.core.patches import (
    AppendPointerPatch,
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
            "manual explicitly."
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

    raw_patches: list[dict[str, Any]] = []
    for finding in report.get("findings", []):
        for patch in finding.get("patches") or []:
            raw_patches.append(patch)

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
    violations: list[tuple[str, str]] = []
    for patch in typed_patches:
        target = Path(patch.target_file).resolve()
        try:
            target.relative_to(manifest_dir_resolved)
        except ValueError:
            violations.append((patch.target_file, str(manifest_dir_resolved)))
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
        grouped[str(Path(patch.target_file).resolve())].append(patch)

    summary = _Summary()
    for target_file, patches in sorted(grouped.items()):
        outcome = _apply_one_file(Path(target_file), patches, apply=apply)
        summary.record(target_file, outcome)

    if json_output:
        typer.echo(json.dumps(summary.as_dict(apply=apply), indent=2))
    else:
        summary.print(apply=apply)


# --- Internals --------------------------------------------------------------


def _emit_input_error(kind: str, message: str, **fields: object) -> None:
    """Emit a structured one-line JSON error on stderr when
    AGENTS_SHIPGATE_AGENT_MODE=1 is set, matching the convention used by
    other commands. Silent otherwise.

    ``fields`` may carry ``next_action`` (legacy single string) and
    ``next_actions`` (ranked list of NextAction dicts) so callers can
    attach recovery hints in the same shape as the global agent-mode
    helper."""
    import os
    import sys

    if os.environ.get("AGENTS_SHIPGATE_AGENT_MODE", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    payload = {"error": kind, "message": message, **fields}
    print(json.dumps(payload, default=str), file=sys.stderr)


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
    original_text = path.read_text(encoding="utf-8")
    current_sha = hashlib.sha256(original_text.encode("utf-8")).hexdigest()

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
    if suffix in {".yaml", ".yml"}:
        new_text = _apply_yaml(original_text, patches)
    elif suffix == ".json":
        new_text = _apply_json(original_text, patches)
    else:
        return _FileOutcome(
            status="error",
            patches_in_group=len(patches),
            error=f"unsupported target format for {path.suffix}",
        )

    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
        )
    )

    if apply and original_text != new_text:
        path.write_text(new_text, encoding="utf-8")
        return _FileOutcome(status="applied", patches_in_group=len(patches), diff=diff)

    return _FileOutcome(
        status="applied" if apply else "dry_run",
        patches_in_group=len(patches),
        diff=diff,
    )


def _apply_yaml(text: str, patches: list[Patch]) -> str:
    yaml = YAML(typ="rt")  # round-trip preserves comments + key order
    yaml.preserve_quotes = True
    yaml.width = 4096
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
        elif isinstance(patch, (SetPointerPatch, AppendPointerPatch)):
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
    elif isinstance(patch, ManualPatch):
        # No-op (filtered out earlier; defensive).
        return


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
