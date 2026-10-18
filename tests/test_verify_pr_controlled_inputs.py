"""#1024: the change under review never supplies its own comparison or exit policy.

Two inputs that decide a pull request's verdict or exit code were read from the
pull request's own head:

* An in-repository ``--diff-from`` was opened in the checkout, replaced the
  ``--base`` scan, and was not a trust root. A change that added a destructive
  tool and regenerated ``ci/base-report.json`` from its own head compared
  against itself and came back ``mergeable``. It is now read as committed at the
  base, and the path is a touched trust root, as are an in-repository
  ``--baseline`` and ``--policy-pack``.
* Without ``--ci-mode`` / ``--fail-on``, ``ci.mode`` and ``ci.fail_on`` came
  from the head's manifest, so flipping ``mode: strict`` to ``mode: advisory``
  (or dropping a ``fail_on`` severity) in the change turned exit 20 into
  exit 0. The mode now resolves to the stricter of the base and head
  manifests, and the base's ``ci.fail_on`` is an exit-only floor: the verdict
  still follows the head's list.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify.git import FileAtRef, regular_file_at_ref
from agents_shipgate.cli.verify.orchestrator import _exit_policy_base_floor
from agents_shipgate.schemas.baseline import BaselineFile
from agents_shipgate.schemas.manifest import CiConfig

runner = CliRunner()

#: Git that ignores the caller's configuration, hooks and exported ``GIT_*``.
_GIT_ENV = {
    **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "agent_weakens_gate"
REFERENCE = "ci/base-report.json"
TRUST_ROOT_TOUCHED = "SHIP-VERIFY-TRUST-ROOT-TOUCHED"

DELETE_TOOL = {
    "name": "docs.delete",
    "description": "Delete a documentation article permanently.",
    "inputSchema": {
        "type": "object",
        "properties": {"article_id": {"type": "string"}},
        "required": ["article_id"],
        "additionalProperties": False,
    },
    "annotations": {"destructiveHint": True},
    "auth": {"type": "oauth2", "scopes": ["docs:admin"]},
    "owner": "docs-platform",
}

REFUND_TOOL = {
    "name": "stripe.create_refund",
    "description": "Create a refund for a customer charge.",
    "inputSchema": {
        "type": "object",
        "properties": {"charge_id": {"type": "string"}, "amount": {"type": "integer"}},
        "required": ["charge_id", "amount"],
        "additionalProperties": False,
    },
    "annotations": {"destructiveHint": True},
    "auth": {"type": "oauth2", "scopes": ["payments:write"]},
    "owner": "payments",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false",
            "-c", f"core.hooksPath={os.devnull}",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _scan_report(repo: Path, out: Path) -> bytes:
    result = runner.invoke(
        app,
        ["scan", "-c", str(repo / "shipgate.yaml"), "--out", str(out), "--format", "json"],
    )
    assert result.exit_code in {0, 20}, result.output
    return (out / "report.json").read_bytes()


def _sample_repo(tmp_path: Path, *, mode: str = "advisory") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "shipgate.yaml").write_text(
        (SAMPLE / "shipgate.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo / "tools.json").write_text(
        (SAMPLE / "tools.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")
    _set_ci(repo, mode=mode)
    return repo


def _set_ci(repo: Path, *, mode: str, fail_on: list[str] | None = None) -> None:
    manifest = repo / "shipgate.yaml"
    block = f"ci:\n  mode: {mode}\n"
    if fail_on is not None:
        block += f"  fail_on: [{', '.join(fail_on)}]\n"
    text, count = re.subn(
        r"ci:\n  mode: \w+\n(?:  fail_on: .*\n)?",
        block,
        manifest.read_text(encoding="utf-8"),
    )
    assert count == 1
    manifest.write_text(text, encoding="utf-8")


def _add_tool(repo: Path, tool: dict) -> None:
    tools = json.loads((repo / "tools.json").read_text(encoding="utf-8"))
    tools["tools"].append(tool)
    (repo / "tools.json").write_text(json.dumps(tools, indent=2) + "\n", encoding="utf-8")


def _add_bound_refund_tool(repo: Path) -> None:
    """A destructive financial tool bound to the root agent: a release blocker."""

    _add_tool(repo, REFUND_TOOL)
    manifest = repo / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "        - {tool: docs.lookup, source_id: docs_tools}\n",
            "        - {tool: docs.lookup, source_id: docs_tools}\n"
            "        - {tool: stripe.create_refund, source_id: docs_tools}\n",
        ),
        encoding="utf-8",
    )


def _regenerate_reference(repo: Path, tmp_path: Path, name: str) -> bytes:
    data = _scan_report(repo, tmp_path / name)
    (repo / REFERENCE).parent.mkdir(parents=True, exist_ok=True)
    (repo / REFERENCE).write_bytes(data)
    return data


def _verify(repo: Path, *args: str, out: Path):
    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace", str(repo),
            "--config", "shipgate.yaml",
            "--out", str(out),
            "--format", "json",
            *args,
        ],
    )
    return result, json.loads(result.stdout)


def _report(out: Path) -> dict:
    return json.loads((out / "report.json").read_text(encoding="utf-8"))


def _fail_policy(out: Path) -> dict:
    return _report(out)["release_decision"]["fail_policy"]


def _touched(out: Path) -> dict[str, str]:
    return {
        finding["evidence"]["changed_file"]: finding["evidence"]["trust_root_class"]
        for finding in _report(out)["findings"]
        if finding["check_id"] == TRUST_ROOT_TOUCHED
    }


def _forged_reference_pr(tmp_path: Path) -> tuple[Path, bytes]:
    """Base commits a scanned reference; the PR adds docs.delete and rescans."""

    repo = _sample_repo(tmp_path)
    base_bytes = _regenerate_reference(repo, tmp_path, "scan-base")
    _commit(repo, "base with a committed comparison reference")
    _git(repo, "checkout", "-q", "-b", "pr")
    _add_tool(repo, DELETE_TOOL)
    head_bytes = _regenerate_reference(repo, tmp_path, "scan-head")
    _commit(repo, "add docs.delete and regenerate the reference")
    assert head_bytes != base_bytes
    return repo, base_bytes


# --- an in-repository --diff-from ---------------------------------------------


def test_a_regenerated_in_repo_reference_cannot_make_the_change_mergeable(
    tmp_path: Path,
) -> None:
    repo, base_bytes = _forged_reference_pr(tmp_path)
    out = tmp_path / "out"

    result, payload = _verify(
        repo, "--base", "main", "--head", "HEAD", "--diff-from", REFERENCE, out=out
    )

    assert result.exit_code == 0, result.output
    assert payload["merge_verdict"] != "mergeable"
    assert payload["can_merge_without_human"] is False
    assert payload["control"]["permissions"]["merge"] is False
    assert payload["capability_review"]["trust_root_touched"] is True
    assert REFERENCE in payload["changed_files"]
    assert _touched(out).get(REFERENCE) == "shipgate_state"
    # The comparison is the base's: the regenerated copy is not what was compared.
    assert (out / "verification-base-report.json").read_bytes() == base_bytes
    assert any(
        f"{REFERENCE} as committed at 'main'" in note for note in payload["base_notes"]
    )


@pytest.mark.parametrize("named_base", [True, False], ids=["named-base", "detected-base"])
@pytest.mark.parametrize("committed", [True, False], ids=["head", "worktree"])
def test_an_in_repo_reference_is_never_read_from_the_change_under_review(
    tmp_path: Path, committed: bool, named_base: bool
) -> None:
    """With a resolvable base, the head's copy is not even parsed.

    A detected base counts: with no ``--base`` and no remote, verify follows
    #1023's ladder to the local ``main``, and the reference is read there.
    """

    repo, base_bytes = _forged_reference_pr(tmp_path)
    # Unreadable as a report: reading it would disable the diff with an error.
    (repo / REFERENCE).write_text("this is not a report\n", encoding="utf-8")
    if committed:
        _commit(repo, "replace the reference")
    out = tmp_path / "out"

    result, payload = _verify(
        repo,
        *(["--base", "main"] if named_base else []),
        *(["--head", "HEAD"] if committed else []),
        "--diff-from", REFERENCE,
        out=out,
    )

    assert result.exit_code == 0, result.output
    assert payload["base_ref"] == "main"
    assert payload["base_status"] == "diff_from_provided"
    assert any(
        f"{REFERENCE} as committed at 'main'" in note for note in payload["base_notes"]
    )
    assert (out / "verification-base-report.json").read_bytes() == base_bytes
    report = _report(out)
    assert report["tool_surface_diff"]["enabled"] is True
    assert "this is not a report" not in json.dumps(report)
    assert payload["merge_verdict"] != "mergeable"
    assert _touched(out).get(REFERENCE) == "shipgate_state"


@pytest.mark.parametrize("held_as", ["absent", "symlink"])
def test_a_reference_the_base_cannot_supply_is_replaced_by_a_base_scan(
    tmp_path: Path, held_as: str
) -> None:
    repo = _sample_repo(tmp_path)
    if held_as == "symlink":
        (repo / "ci").mkdir()
        (repo / REFERENCE).symlink_to("../tools.json")
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    if held_as == "symlink":
        (repo / REFERENCE).unlink()
    _add_tool(repo, DELETE_TOOL)
    _regenerate_reference(repo, tmp_path, "scan-head")
    _commit(repo, "add docs.delete and a reference generated from it")
    out = tmp_path / "out"

    result, payload = _verify(
        repo, "--base", "main", "--head", "HEAD", "--diff-from", REFERENCE, out=out
    )

    assert result.exit_code == 0, result.output
    # Compared with a scan of the base, as though --diff-from were absent. A
    # base tree holding a link cannot be scanned either, and that comparison
    # fails closed exactly as it does without --diff-from.
    assert payload["base_status"] == (
        "succeeded" if held_as == "absent" else "archive_failed"
    )
    reason = (
        "'main' does not contain it"
        if held_as == "absent"
        else "it could not be read at 'main': it is not a regular file"
    )
    assert f"Did not use --diff-from {REFERENCE}: {reason}." in " ".join(
        payload["base_notes"]
    )
    assert payload["merge_verdict"] != "mergeable"
    assert payload["can_merge_without_human"] is False
    assert _touched(out).get(REFERENCE) == "shipgate_state"


def test_a_reference_outside_the_repository_is_read_where_it_is(tmp_path: Path) -> None:
    """The operator's own file, outside anything the change can edit."""

    repo = _sample_repo(tmp_path)
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    _add_tool(repo, DELETE_TOOL)
    _commit(repo, "add docs.delete")
    external = tmp_path / "operator" / "base-report.json"
    external.parent.mkdir()
    external.write_bytes(_scan_report(repo, tmp_path / "scan-operator"))
    out = tmp_path / "out"

    result, payload = _verify(
        repo, "--base", "main", "--head", "HEAD", "--diff-from", str(external), out=out
    )

    assert result.exit_code == 0, result.output
    assert payload["base_status"] == "diff_from_provided"
    assert (out / "verification-base-report.json").read_bytes() == external.read_bytes()
    assert any(
        note.startswith("Using explicit diff reference:") for note in payload["base_notes"]
    )
    assert _touched(out) == {}


def test_in_repo_baseline_and_policy_pack_are_trust_roots(tmp_path: Path) -> None:
    repo = _sample_repo(tmp_path)
    baseline = repo / "ci" / "accepted.json"
    policy = repo / "rules" / "org.yaml"
    baseline.parent.mkdir()
    policy.parent.mkdir()
    baseline.write_text(
        BaselineFile(
            created_at="2026-10-01T00:00:00Z", source_report_run_id="base-baseline"
        ).model_dump_json(),
        encoding="utf-8",
    )
    policy.write_text('name: Org policy\nversion: "1"\nrules: []\n', encoding="utf-8")
    _commit(repo, "base with configured inputs")
    _git(repo, "checkout", "-q", "-b", "pr")
    baseline.write_text(
        BaselineFile(
            created_at="2026-10-02T00:00:00Z", source_report_run_id="head-baseline"
        ).model_dump_json(),
        encoding="utf-8",
    )
    policy.write_text('name: Org policy, edited\nversion: "1"\nrules: []\n', encoding="utf-8")
    _commit(repo, "edit both")
    out = tmp_path / "out"

    result, payload = _verify(
        repo,
        "--base", "main",
        "--head", "HEAD",
        "--baseline", "ci/accepted.json",
        "--policy-pack", "rules/org.yaml",
        out=out,
    )

    assert result.exit_code == 0, result.output
    assert _touched(out) == {
        "ci/accepted.json": "shipgate_state",
        "rules/org.yaml": "policy",
    }
    assert payload["capability_review"]["trust_root_touched"] is True
    assert payload["can_merge_without_human"] is False


def test_a_file_at_a_ref_is_read_only_as_a_regular_file(tmp_path: Path) -> None:
    """A link is never followed, and an oversize blob is not an absent one."""

    repo = _sample_repo(tmp_path)
    (repo / "ci").mkdir()
    (repo / "ci" / "report.json").write_text('{"ok": true}\n', encoding="utf-8")
    (repo / "ci" / "link.json").symlink_to("report.json")
    (repo / "ci" / "tree").mkdir()
    (repo / "ci" / "tree" / "member.json").write_text("{}\n", encoding="utf-8")
    _commit(repo, "inputs")

    def read(path: str, max_bytes: int = 1024) -> FileAtRef:
        return regular_file_at_ref(repo, "HEAD", Path(path), max_bytes=max_bytes)

    assert read("ci/report.json") == FileAtRef("present", data=b'{"ok": true}\n')
    assert read("ci/missing.json").state == "absent"
    assert read("ci/link.json").state == "unreadable"
    assert read("ci/link.json").reason == "it is not a regular file"
    assert read("ci/tree").state == "unreadable"
    assert read("ci/report.json", max_bytes=4).state == "unreadable"
    assert read("ci/*.json").state == "absent"
    assert regular_file_at_ref(
        repo, "no-such-ref", Path("ci/report.json"), max_bytes=1024
    ).state == "unreadable"


# --- ci.mode and ci.fail_on ----------------------------------------------------


def _flipped_to_advisory(tmp_path: Path) -> Path:
    """Base declares strict; the PR adds a blocking tool and declares advisory."""

    repo = _sample_repo(tmp_path, mode="strict")
    _commit(repo, "strict base")
    _git(repo, "checkout", "-q", "-b", "pr")
    _add_bound_refund_tool(repo)
    _set_ci(repo, mode="advisory")
    _commit(repo, "add a refund tool and relax the gate")
    return repo


def test_a_change_cannot_lower_its_own_ci_mode(tmp_path: Path) -> None:
    repo = _flipped_to_advisory(tmp_path)
    out = tmp_path / "out"

    result, payload = _verify(repo, "--base", "main", "--head", "HEAD", out=out)

    assert payload["merge_verdict"] == "blocked"
    assert result.exit_code == 20, result.output
    assert _fail_policy(out)["ci_mode"] == "strict"
    assert payload["mode"] == "strict"
    assert any(
        "declares ci.mode: advisory and 'main' declares strict" in note
        for note in payload["base_notes"]
    )


def test_an_explicit_ci_mode_remains_the_operators_override(tmp_path: Path) -> None:
    repo = _flipped_to_advisory(tmp_path)
    out = tmp_path / "out"

    result, payload = _verify(
        repo, "--base", "main", "--head", "HEAD", "--ci-mode", "advisory", out=out
    )

    assert result.exit_code == 0, result.output
    assert payload["merge_verdict"] == "blocked"
    assert _fail_policy(out)["ci_mode"] == "advisory"
    assert not any("ci.mode" in note for note in payload["base_notes"])


def test_a_change_can_still_tighten_its_own_ci_mode(tmp_path: Path) -> None:
    repo = _sample_repo(tmp_path, mode="advisory")
    _commit(repo, "advisory base")
    _git(repo, "checkout", "-q", "-b", "pr")
    _add_bound_refund_tool(repo)
    _set_ci(repo, mode="strict")
    _commit(repo, "add a refund tool and adopt strict")
    out = tmp_path / "out"

    result, _payload = _verify(repo, "--base", "main", "--head", "HEAD", out=out)

    assert result.exit_code == 20, result.output
    assert _fail_policy(out)["ci_mode"] == "strict"


@pytest.mark.parametrize(
    ("base", "head", "flags", "expected"),
    [
        # A head that declares what the base declares is scanned as before.
        ({"mode": "strict"}, {"mode": "strict"}, (None, None), (None, None, False)),
        ({"mode": "advisory"}, {"mode": "advisory"}, (None, None), (None, None, False)),
        (
            {"mode": "advisory", "fail_on": ["high"]},
            {"mode": "advisory", "fail_on": ["high"]},
            (None, None),
            (None, ["high"], False),
        ),
        # Tightening is the head's to do.
        ({"mode": "advisory"}, {"mode": "strict"}, (None, None), (None, None, False)),
        (
            {"mode": "advisory", "fail_on": ["high"]},
            {"mode": "advisory", "fail_on": ["critical", "high"]},
            (None, None),
            (None, ["high"], False),
        ),
        # Lowering is not.
        ({"mode": "strict"}, {"mode": "advisory"}, (None, None), ("strict", None, True)),
        (
            {"mode": "advisory", "fail_on": ["critical", "high"]},
            {"mode": "advisory", "fail_on": ["critical"]},
            (None, None),
            (None, ["critical", "high"], True),
        ),
        # The Action's path: --ci-mode is the operator's, ci.fail_on still floors.
        (
            {"mode": "strict", "fail_on": ["high"]},
            {"mode": "advisory"},
            ("advisory", None),
            ("advisory", ["high"], True),
        ),
        # --fail-on is the operator's too.
        (
            {"mode": "advisory", "fail_on": ["high"]},
            {"mode": "advisory"},
            (None, ["low"]),
            (None, None, False),
        ),
    ],
)
def test_the_exit_policy_floor_holds_what_the_head_would_lower(
    base: dict, head: dict, flags: tuple, expected: tuple
) -> None:
    def manifest(ci: dict) -> str:
        repo_manifest = (SAMPLE / "shipgate.yaml").read_text(encoding="utf-8")
        block = f"ci:\n  mode: {ci['mode']}\n"
        if "fail_on" in ci:
            block += f"  fail_on: [{', '.join(ci['fail_on'])}]\n"
        return re.sub(r"ci:\n  mode: \w+\n", block, repo_manifest)

    ci_mode, fail_on = flags
    resolved, floor, notes = _exit_policy_base_floor(
        head_manifest_text=manifest(head),
        base_ci=CiConfig(**base),
        base="main",
        ci_mode=ci_mode,
        fail_on=fail_on,
    )

    expected_mode, expected_floor, noted = expected
    assert resolved == (expected_mode if ci_mode is None else ci_mode)
    assert floor == expected_floor
    assert bool(notes) is noted


@pytest.mark.parametrize("operator_mode", [None, "advisory"])
def test_a_loosened_fail_on_fails_ci_while_the_verdict_stays_review(
    tmp_path: Path, operator_mode: str | None
) -> None:
    """The base's ci.fail_on floors the exit code and nothing else.

    ``fail_on`` also sets the blocker severities, so the verdict keeps the
    head's list: the change's own ``SHIP-VERIFY-POLICY-WEAKENED`` (high) stays
    a human-review item, as the governance benchmark's
    ``ci-advisory-from-strict`` pins. But the change cannot lower its own exit
    code: under the base's list that high finding fails CI until a human
    handles it. ``--ci-mode`` (the Action always passes one) does not lift the
    floor; ``--fail-on`` does.
    """

    repo = _sample_repo(tmp_path, mode="strict")
    _set_ci(repo, mode="strict", fail_on=["critical", "high"])
    _commit(repo, "strict base failing on critical and high")
    _git(repo, "checkout", "-q", "-b", "pr")
    _set_ci(repo, mode="advisory")
    _commit(repo, "relax the gate")
    mode_args = ["--ci-mode", operator_mode] if operator_mode else []
    out = tmp_path / "out"

    result, payload = _verify(repo, "--base", "main", "--head", "HEAD", *mode_args, out=out)

    assert payload["merge_verdict"] == "human_review_required"
    assert payload["can_merge_without_human"] is False
    assert payload["capability_review"]["policy_weakening_proven"] is True
    policy = _fail_policy(out)
    assert policy["ci_mode"] == (operator_mode or "strict")
    assert policy["fail_on"] == (["critical"] if operator_mode is None else [])
    assert policy["would_fail_ci"] is True
    assert policy["exit_code"] == 20
    assert result.exit_code == 20, result.output
    assert any(
        "ci.fail_on leaves out" in note and "'main' declares" in note
        for note in payload["base_notes"]
    )

    out = tmp_path / "explicit"
    result, payload = _verify(
        repo, "--base", "main", "--head", "HEAD", *mode_args, "--fail-on", "critical",
        out=out,
    )
    assert payload["merge_verdict"] == "human_review_required"
    assert result.exit_code == 0, result.output
    assert not any("ci.fail_on" in note for note in payload["base_notes"])


def test_dropping_fail_on_beside_a_blocking_tool_still_fails_ci(tmp_path: Path) -> None:
    """The issue's shape for ``fail_on``: a blocked change cannot exit 0."""

    repo = _sample_repo(tmp_path)
    _set_ci(repo, mode="advisory", fail_on=["high"])
    _commit(repo, "advisory base failing on high")
    _git(repo, "checkout", "-q", "-b", "pr")
    _add_bound_refund_tool(repo)
    _set_ci(repo, mode="advisory")
    _commit(repo, "add a refund tool and drop fail_on")
    out = tmp_path / "out"

    result, payload = _verify(
        repo, "--base", "main", "--head", "HEAD", "--ci-mode", "advisory", out=out
    )

    assert payload["merge_verdict"] == "blocked"
    assert result.exit_code == 20, result.output
    assert _fail_policy(out)["fail_on"] == []


def test_ignoring_a_control_no_suppression_can_waive_still_fails_ci(tmp_path: Path) -> None:
    """#1029: a change that ignores its own blocker cannot exit 0 under fail_on.

    The change suppresses both critical findings the refund tool raises. The
    approval finding is waivable and leaves the gate. The rollback control is
    a mandatory current-surface control, so the decision keeps it a blocker,
    and the exit code now reads that blocker too: in ``report.json``, in
    ``verifier.json`` and in the process exit.
    """

    repo = _sample_repo(tmp_path)
    _set_ci(repo, mode="advisory", fail_on=["critical"])
    _add_bound_refund_tool(repo)
    _commit(repo, "advisory base failing on critical, with a refund tool")
    _git(repo, "checkout", "-q", "-b", "pr")
    manifest = repo / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\nchecks:\n  ignore:\n"
        + "".join(
            f"    - check_id: {check_id}\n"
            "      tool: stripe.create_refund\n"
            "      reason: accepted for this test\n"
            for check_id in (
                "SHIP-POLICY-APPROVAL-MISSING",
                "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
            )
        ),
        encoding="utf-8",
    )
    _commit(repo, "ignore the refund tool's critical findings")
    out = tmp_path / "out"

    result, payload = _verify(repo, "--base", "main", "--head", "HEAD", out=out)

    assert payload["merge_verdict"] == "blocked"
    blockers = {item["check_id"] for item in payload["release_decision"]["blockers"]}
    assert "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING" in blockers
    assert "SHIP-POLICY-APPROVAL-MISSING" not in blockers
    failing = {
        "ci_mode": "advisory",
        "fail_on": ["critical"],
        "new_findings_only": False,
        "would_fail_ci": True,
        "exit_code": 20,
    }
    assert _fail_policy(out) == failing
    assert payload["release_decision"]["fail_policy"] == failing
    assert result.exit_code == 20, result.output
