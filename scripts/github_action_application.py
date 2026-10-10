"""Publish the CLI's advisory application comparison, without a gate verdict."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER = "<!-- agents-shipgate-application-comment -->"
STATUSES = frozenset({"compared", "partial", "not_established"})
INCOMPATIBLE = (
    "FAIL_ON", "FAIL_ON_MERGE_VERDICTS", "BASELINE", "DIFF_FROM", "POLICY_PACKS",
    "ATTESTATION", "ORG_BUNDLE", "HOST_AUDIT", "ORG_STATUS", "CHECK_RUN",
)


def output_directory(workspace: Path, value: str) -> Path:
    """Use a fresh directory, so a refusal cannot publish a prior comparison."""
    if "\n" in value or "\r" in value:
        raise ValueError("output_dir cannot contain line breaks.")
    directory = Path(os.path.abspath(workspace / value))
    if directory == workspace or not directory.is_relative_to(workspace):
        raise ValueError("Application output_dir must be a directory inside GITHUB_WORKSPACE.")
    from agents_shipgate.core.trust_roots import trust_root_class_for

    relative = directory.relative_to(workspace)
    for parent in [directory, *directory.parents]:
        if parent == workspace:
            break
        if parent.is_symlink():
            raise ValueError("Application output_dir cannot traverse a symbolic link.")
    parts = tuple(part.casefold() for part in relative.parts)
    if (
        ".git" in parts or parts[:2] == (".github", "workflows")
        or trust_root_class_for(relative.as_posix() + "/application.json")
    ):
        raise ValueError("Application output_dir cannot be a Git metadata or protected surface.")
    directory.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="application-", dir=directory))


def code_block(text: str, *, limit: int) -> str:
    # Indentation preserves source text without letting it close a Markdown fence.
    lines = []
    remaining = limit
    for line in text.splitlines():
        chunk = "    " + line + "\n"
        lines.append(chunk[:remaining])
        remaining -= len(chunk)
        if remaining <= 0:
            return "".join(lines) + "\n\nDetail was shortened here. Read the complete workflow artifact.\n"
    return "".join(lines)


def markdown(text: str, *, short: bool = False) -> str:
    return MARKER + "\n## Agents Shipgate application review\n\n" + code_block(
        text, limit=4800 if short else 800_000,
    )


def comparison_arguments(env: dict[str, str], workspace: Path) -> list[str]:
    incompatible = [key.lower() for key in INCOMPATIBLE if env.get(key, "") not in {"", "false"}]
    if env.get("CI_MODE", "advisory") != "advisory":
        incompatible.append("ci_mode")
    if env.get("VERIFY_MODE", "verify") != "verify":
        incompatible.append("verify_mode")
    if incompatible:
        raise ValueError("Application mode cannot use verifier options: " + ", ".join(incompatible))
    base = env.get("INPUT_BASE_REF") or env.get("APPLICATION_BASE_SHA")
    head = env.get("INPUT_HEAD_REF") or env.get("APPLICATION_HEAD_SHA")
    if not base or not head:
        raise ValueError("Application mode requires the PR base and head SHAs, or both base_ref and head_ref.")
    arguments = [
        sys.executable, "-P", "-m", "agents_shipgate", "diff", "--application",
        "--workspace", str(workspace), "--base", base, "--head", head, "--json",
    ]
    if env.get("APPLICATION_SCOPE"):
        arguments += ["--scope", env["APPLICATION_SCOPE"]]
    return arguments


def fail_policy(status: str, policy: str) -> int:
    tokens = {token.strip() for token in policy.split(",") if token.strip()}
    if tokens - STATUSES:
        raise ValueError("application_fail_on supports only compared, partial, not_established.")
    return 20 if status in tokens else 0


def render(payload: dict) -> str:
    from agents_shipgate.cli.application_diff import _print_comparison

    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        _print_comparison(payload, payload["base"]["compared_commit"], payload["head"]["compared_commit"])
    return stream.getvalue()


def run(env: dict[str, str]) -> int:
    workspace = Path(env.get("GITHUB_WORKSPACE") or os.getcwd()).resolve()
    values: dict[str, object] = {
        "application_status": "refused", "application_reports": "",
        "application_json": "", "application_markdown": "", "exit_code": 2,
    }
    directory = None
    try:
        directory = output_directory(workspace, env.get("OUTPUT_DIR") or "agents-shipgate-reports")
        values["application_reports"] = str(directory)
        fail_policy("", env.get("APPLICATION_FAIL_ON", ""))
        arguments = comparison_arguments(env, workspace)
        completed = subprocess.run(arguments, cwd=workspace, env={**env, "NO_COLOR": "1"},
                                   text=True, capture_output=True, check=False)
        if completed.returncode:
            raise ValueError(
                "Application comparison did not run.\n" + (completed.stderr or completed.stdout).strip()
                + "\nUse actions/checkout with fetch-depth: 0 and full objects; make both refs locally available. "
                "For a partial clone, follow the CLI's objects_missing hydration command. The Action never fetches."
            )
        payload = json.loads(completed.stdout)
        from agents_shipgate.cli.application_diff import SCHEMA_VERSION

        if payload.get("application_comparison_schema_version") != SCHEMA_VERSION:
            raise ValueError("The installed CLI did not emit a supported application comparison.")
        status = payload["comparison_status"]
        if status not in STATUSES:
            raise ValueError("The CLI emitted an unknown application comparison status.")
        json_path = directory / "application-comparison.json"
        json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        text = render(payload)
        values.update(application_status=status, application_json=str(json_path),
                      exit_code=fail_policy(status, env.get("APPLICATION_FAIL_ON", "")))
    except (ValueError, OSError, KeyError, ImportError) as error:
        text = f"Application comparison refused: {error}\nNo application comparison or merge verdict is established."
        if directory is not None:
            error_path = directory / "application-error.json"
            error_path.write_text(json.dumps({"error": "application_comparison_refused", "message": str(error)}) + "\n",
                                  encoding="utf-8")
    if directory is not None:
        review = directory / "application-review.md"
        review.write_text(markdown(text), encoding="utf-8")
        (directory / "pr-comment.md").write_text(markdown(text, short=True), encoding="utf-8")
        values["application_markdown"] = str(review)
    if env.get("GITHUB_STEP_SUMMARY"):
        with Path(env["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as stream:
            stream.write(markdown(text))
    if env.get("GITHUB_OUTPUT"):
        with Path(env["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    print(text)
    return int(values["exit_code"])


if __name__ == "__main__":
    raise SystemExit(run(dict(os.environ)))
