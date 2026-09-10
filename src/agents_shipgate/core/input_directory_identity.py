"""Validate and reconfirm reader-selected input directory membership."""

from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.static_inputs import DEFAULT_STATIC_INPUT_FILES, StaticInputSnapshot
from agents_shipgate.schemas.verification_identity import VerificationPlan, validate_portable_path


def _path(value: object, *, root_allowed: bool = False) -> str:
    if (not isinstance(value, str) or ":" in value
            or (value == "." and not root_allowed)):
        raise ValueError("invalid directory input path")
    return validate_portable_path(value)


def directory_input_exclusions(plan: VerificationPlan) -> list[str]:
    """Validate the whole census before any filesystem access or exclusions."""

    raw = plan.inputs.options.get("input_directories")
    if raw is None:
        raise ValueError("input directory capture is unavailable; re-run verification")
    if (not isinstance(raw, dict)
            or set(raw) != {"version", "source", "excluded_paths", "directories", "unconfirmable_paths"}
            or type(raw["version"]) is not int or raw["version"] != 1
            or raw["source"] != ("git_blob" if plan.subject.git.snapshot_kind == "committed_tree" else "worktree")
            or not all(isinstance(raw[key], list) for key in (
                "excluded_paths", "directories", "unconfirmable_paths"
            ))):
        raise ValueError("invalid input directory identity")
    for key in ("excluded_paths", "unconfirmable_paths"):
        values = [_path(value, root_allowed=key == "unconfirmable_paths") for value in raw[key]]
        if values != sorted(set(values)):
            raise ValueError("directory input paths must be sorted and unique")
    if ".git" not in raw["excluded_paths"]:
        raise ValueError("directory input identity lacks its metadata exclusion")
    if raw["unconfirmable_paths"]:
        raise ValueError("an input directory could not be captured; repair it and re-run verification")
    paths = []
    entries = 0
    for row in raw["directories"]:
        if not isinstance(row, dict) or set(row) != {"path", "members"}:
            raise ValueError("invalid input directory row")
        path = _path(row["path"], root_allowed=True)
        if any(Path(path) == Path(excluded) or Path(excluded) in Path(path).parents
               for excluded in raw["excluded_paths"]):
            raise ValueError("input directory overlaps an excluded path")
        paths.append(path)
        if not isinstance(row["members"], list):
            raise ValueError("invalid input directory members")
        entries += len(row["members"]) + 1
        if entries > DEFAULT_STATIC_INPUT_FILES:
            raise ValueError("input directory census exceeds its aggregate entry limit")
        names = []
        for member in row["members"]:
            if (not isinstance(member, dict) or set(member) != {"name", "kind"}
                    or not isinstance(member["kind"], str)
                    or member["kind"] not in {"file", "directory", "symlink", "special"}):
                raise ValueError("invalid input directory member")
            name = _path(member["name"])
            if "/" in name:
                raise ValueError("directory member must be one lexical name")
            names.append(name)
        if names != sorted(set(names)):
            raise ValueError("directory members must be sorted and unique")
    if paths != sorted(set(paths)):
        raise ValueError("input directories must be sorted and unique")
    return raw["excluded_paths"]


def validate_directory_inputs(plan: VerificationPlan, *, snapshot: StaticInputSnapshot) -> None:
    """Use the same session as file bytes and the same source-discovery projection."""

    raw = plan.inputs.options["input_directories"]
    for row in raw["directories"]:
        snapshot.enumerate_input_directory(snapshot.root / row["path"])
    observed = snapshot.input_directory_identity(source=raw["source"])
    if observed["directories"] != raw["directories"]:
        raise ValueError("input directory membership changed since verification")
