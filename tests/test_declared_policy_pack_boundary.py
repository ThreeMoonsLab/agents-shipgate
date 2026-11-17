"""#1128: ``check`` and the Claude Code hook protect the policy packs a manifest declares.

#1075 made ``verify`` treat every ``checks.policy_packs[].path`` the base or
head manifest declares as a ``policy`` trust root, wherever it lives. The local
half still classified by the trust-root table alone. A change that edited only
``vendor/shipgate-policies/org-release.yaml`` got ``allow`` from
``shipgate check``, the installed PreToolUse hook let the edit land without
asking, and the boundary assessment ``verify`` publishes had a protected-surface
row for ``policies/org.yaml`` and none for the vendored pack. All of them now
resolve the declarations through ``core.trust_roots.manifest_policy_pack_paths``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.install_hooks import _hook_script_text
from agents_shipgate.cli.main import app
from agents_shipgate.config.loader import load_manifest_text
from agents_shipgate.core.trust_roots import (
    is_manifest_policy_pack,
    manifest_policy_pack_paths,
)

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "samples" / "agent_weakens_gate"
LAUNCHER = REPO_ROOT / "shipgate"
#: The layout docs/organization.md recommends for a vendored organization pack.
VENDORED_PACK = "vendor/shipgate-policies/org-release.yaml"
PROTECTED = "BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED"
DECLARED_EVIDENCE = {"kind": "protected_surface_unclassified", "trust_root_class": "policy"}

#: Git that ignores the caller's configuration, hooks and exported ``GIT_*``.
_GIT_ENV = {
    **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
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


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def _write_pack(path: Path, *, version: str = "1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'id: org-release\nname: Org release policy\nversion: "{version}"\nrules: []\n',
        encoding="utf-8",
    )


def _declare_packs(manifest: Path, *packs: str) -> None:
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\nchecks:\n  policy_packs:\n"
        + "".join(f"    - path: {pack}\n" for pack in packs),
        encoding="utf-8",
    )


def _repo(tmp_path: Path, *packs: str, declared: bool = True, scope: str = "") -> Path:
    """A ``main`` whose manifest declares ``packs``, checked out on ``pr``."""

    repo = tmp_path / "repo"
    gate = repo / scope if scope else repo
    gate.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    for name in ("shipgate.yaml", "tools.json"):
        (gate / name).write_text((SAMPLE / name).read_text(encoding="utf-8"), encoding="utf-8")
    (repo / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")
    for pack in packs:
        _write_pack(gate / pack)
    if declared and packs:
        _declare_packs(gate / "shipgate.yaml", *packs)
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    return repo


def _check(repo: Path, *args: str) -> dict:
    result = runner.invoke(
        app,
        ["check", "--workspace", str(repo), "--agent", "claude-code",
         "--format", "agent-boundary-json", *args],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _rows(payload: dict) -> dict[str, dict]:
    return {
        row["path"]: row["evidence"] for row in payload["violations"] if row["id"] == PROTECTED
    }


def _assert_routed_to_review(payload: dict) -> None:
    assert payload["decision"] == "require_review"
    # A gate-governing policy row never takes the graded agent route.
    assert payload["control"]["state"] in {"review_publishable", "human_review_required"}
    assert payload["control"]["human_review"]["required"] is True
    assert payload["control"]["permissions"]["merge"] is False
    assert payload["control"]["permissions"]["report_complete"] is False


# --- check -------------------------------------------------------------------


@pytest.mark.parametrize("pack", [VENDORED_PACK, "rules/org.yaml"])
def test_check_routes_an_edit_to_a_declared_pack_to_review(tmp_path: Path, pack: str) -> None:
    """The reported defect: the same edit under ``policies/`` already got review."""

    repo = _repo(tmp_path, pack)
    _write_pack(repo / pack, version="2")
    _commit(repo, "edit only the pack")

    payload = _check(repo, "--base", "main", "--head", "HEAD")

    assert payload["changed_files"] == [pack]
    assert _rows(payload) == {pack: DECLARED_EVIDENCE}
    assert payload["input_coverage"] == "complete"
    _assert_routed_to_review(payload)


def test_an_undeclared_file_is_not_protected(tmp_path: Path) -> None:
    """Only a declaration makes a file outside the table a policy surface."""

    repo = _repo(tmp_path, VENDORED_PACK, declared=False)
    _write_pack(repo / VENDORED_PACK, version="2")
    _commit(repo, "edit a file no manifest declares")

    payload = _check(repo, "--base", "main", "--head", "HEAD")

    assert payload["decision"] == "allow"
    assert _rows(payload) == {}


def test_removing_the_declaration_does_not_unprotect_the_pack(tmp_path: Path) -> None:
    """Judged against the base's declaration, as ``verify`` judges it."""

    repo = _repo(tmp_path, VENDORED_PACK)
    (repo / "shipgate.yaml").write_text(
        (SAMPLE / "shipgate.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    _write_pack(repo / VENDORED_PACK, version="2")
    _commit(repo, "drop the declaration and edit the pack")

    payload = _check(repo, "--base", "main", "--head", "HEAD")

    assert _rows(payload) == {
        "shipgate.yaml": {"kind": "protected_surface_unclassified"},
        VENDORED_PACK: DECLARED_EVIDENCE,
    }
    _assert_routed_to_review(payload)


def test_a_pack_the_change_declares_is_protected(tmp_path: Path) -> None:
    """The head manifest's declarations count too, beside the manifest edit."""

    repo = _repo(tmp_path)
    _write_pack(repo / VENDORED_PACK)
    _declare_packs(repo / "shipgate.yaml", VENDORED_PACK)
    _commit(repo, "declare and add a pack")

    payload = _check(repo, "--base", "main", "--head", "HEAD")

    assert _rows(payload)[VENDORED_PACK] == DECLARED_EVIDENCE
    assert "shipgate.yaml" in _rows(payload)


@pytest.mark.parametrize(
    "selection",
    [("--base", "HEAD"), (), ("--base", "main")],
    ids=["uncommitted", "flagless", "base-alone"],
)
def test_an_uncommitted_edit_to_a_declared_pack_is_protected(
    tmp_path: Path, selection: tuple[str, ...]
) -> None:
    repo = _repo(tmp_path, VENDORED_PACK)
    _write_pack(repo / VENDORED_PACK, version="2")

    payload = _check(repo, *selection)

    assert _rows(payload) == {VENDORED_PACK: DECLARED_EVIDENCE}
    _assert_routed_to_review(payload)


def test_a_provided_diff_reads_the_working_tree_manifest(tmp_path: Path) -> None:
    """``--diff`` names no commit, so the working-tree manifest declares the pack."""

    repo = _repo(tmp_path, VENDORED_PACK)
    _write_pack(repo / VENDORED_PACK, version="2")
    diff = tmp_path / "change.diff"
    diff.write_text(_git(repo, "diff") + "\n", encoding="utf-8")

    payload = _check(repo, "--diff", str(diff))

    assert _rows(payload) == {VENDORED_PACK: DECLARED_EVIDENCE}
    _assert_routed_to_review(payload)


def test_a_scoped_manifest_binds_the_pack_beside_it(tmp_path: Path) -> None:
    """A pack path resolves against the manifest's directory, as the scan does."""

    repo = _repo(tmp_path, "rules/org.yaml", scope="services/mailer")
    _write_pack(repo / "rules" / "org.yaml")
    _commit(repo, "a root file that shares the pack's name")
    _write_pack(repo / "services" / "mailer" / "rules" / "org.yaml", version="2")
    _write_pack(repo / "rules" / "org.yaml", version="2")
    _commit(repo, "edit both")

    payload = _check(
        repo, "--config", "services/mailer/shipgate.yaml", "--base", "main", "--head", "HEAD"
    )

    assert _rows(payload) == {"services/mailer/rules/org.yaml": DECLARED_EVIDENCE}


def test_a_declared_pack_under_policies_keeps_its_row(tmp_path: Path) -> None:
    """The table answers first: declaring ``policies/org.yaml`` moves nothing."""

    results: dict[bool, dict] = {}
    for declared in (False, True):
        root = tmp_path / ("declared" if declared else "undeclared")
        root.mkdir()
        repo = _repo(root, "policies/org.yaml", declared=declared)
        _write_pack(repo / "policies" / "org.yaml", version="2")
        _commit(repo, "edit the pack")
        results[declared] = _check(repo, "--base", "main", "--head", "HEAD")

    assert _rows(results[True]) == {"policies/org.yaml": {"kind": "protected_surface_unclassified"}}
    for key in ("violations", "finding_fingerprints", "decision", "host_coverage", "affected_hosts"):
        assert results[True][key] == results[False][key], key
    assert results[True]["control"]["state"] == results[False]["control"]["state"]


# --- verify publishes the row check publishes ---------------------------------


def _verify_findings(repo: Path, out: Path) -> list[dict]:
    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--base", "main",
         "--head", "HEAD", "--ci-mode", "advisory", "--out", str(out), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    return json.loads((out / "report.json").read_text(encoding="utf-8"))["findings"]


def test_verify_publishes_the_boundary_row_check_publishes(tmp_path: Path) -> None:
    """One diff, one boundary answer, as for ``policies/org.yaml``."""

    repo = _repo(tmp_path, VENDORED_PACK)
    _write_pack(repo / VENDORED_PACK, version="2")
    _commit(repo, "edit only the pack")

    check_rows = [
        (row["check_id"], row["path"], row["evidence"])
        for row in _check(repo, "--base", "main", "--head", "HEAD")["violations"]
    ]
    findings = _verify_findings(repo, tmp_path / "out")
    boundary = [
        (item["check_id"], item["source"]["path"], item["evidence"])
        for item in findings
        if item["category"] == "host_boundary"
    ]
    touched = [item for item in findings if item["check_id"] == "SHIP-VERIFY-TRUST-ROOT-TOUCHED"]

    assert boundary == check_rows == [
        ("SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED", VENDORED_PACK, DECLARED_EVIDENCE)
    ]
    assert [item["evidence"]["trust_root_class"] for item in touched] == ["policy"]


# --- the one resolution --------------------------------------------------------


_DECLARED = [
    "rules/../vendor/org.yaml",
    "./rules/org.yaml",
    "../../../outside.yaml",
    "../shared/org.yaml",
    "",
    "   ",
]


def _manifest(*packs: str):
    sample = (SAMPLE / "shipgate.yaml").read_text(encoding="utf-8")
    return load_manifest_text(
        sample + "\nchecks:\n  policy_packs:\n" + "".join(f"    - path: {json.dumps(p)}\n" for p in packs)
    )


def test_manifest_policy_pack_paths_resolve_as_the_scan_resolves_them(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    manifest = _manifest(*_DECLARED, str(root / "absolute" / "inside.yaml"), "rules/org.yaml")

    paths = manifest_policy_pack_paths(
        (manifest, None), repository_root=root,
        config_relative=Path("services/mailer/shipgate.yaml"),
    )

    assert paths == (
        "services/mailer/vendor/org.yaml",
        "services/mailer/rules/org.yaml",
        "services/shared/org.yaml",
        "absolute/inside.yaml",
    )
    assert manifest_policy_pack_paths((None,), repository_root=root, config_relative=Path("x")) == ()
    # Several versions: declaration order, the first spelling kept.
    assert manifest_policy_pack_paths(
        (_manifest("b.yaml", "a.yaml"), _manifest("a.yaml", "c.yaml")),
        repository_root=root, config_relative=Path("shipgate.yaml"),
    ) == ("b.yaml", "a.yaml", "c.yaml")
    assert is_manifest_policy_pack("services/mailer/./Rules/ORG.yaml", paths)
    assert not is_manifest_policy_pack("services/mailer/rules", paths)
    assert not is_manifest_policy_pack("", paths)


def test_the_hook_resolves_declared_packs_as_core_does(tmp_path: Path) -> None:
    """The hook's mirror of the resolution, kept in step over one table."""

    hook = _hook()
    root = tmp_path / "repo"
    declared = [
        *_DECLARED, "vendor\\windows.yaml", str(root / "absolute" / "inside.yaml"), "rules/org.yaml",
    ]
    for config in ("shipgate.yaml", "services/mailer/shipgate.yaml"):
        core = manifest_policy_pack_paths(
            (_manifest(*declared),), repository_root=root, config_relative=Path(config)
        )
        # The hook resolves what `org policy-packs` reports, which is the
        # declared path after the loader's own normalization.
        mirrored = [
            hook["_resolve_policy_pack"](root, config, pack.path)
            for pack in _manifest(*declared).checks.policy_packs
        ]
        assert tuple(dict.fromkeys(path for path in mirrored if path)) == core


# --- the installed hook ---------------------------------------------------------


def _hook() -> dict:
    namespace: dict = {"__name__": "hook_under_test"}
    exec(compile(_hook_script_text(), "generated-hook.py", "exec"), namespace)
    namespace["_cli"] = lambda: [str(LAUNCHER)]
    # A loaded runner can take longer than an event's budget to start the CLI,
    # and a CLI that did not answer is asked again, which the counts below see.
    namespace["POLICY_PACK_RESOLUTION_TIMEOUT_SECONDS"] = 120
    return namespace


def _args(config: str = "shipgate.yaml", base: str = "main") -> argparse.Namespace:
    return argparse.Namespace(config=config, base=base, head="", ci_mode="advisory")


def _event(repo: Path, path: str, *, session: str = "S1") -> dict:
    return {
        "session_id": session,
        "permission_mode": "default",
        "tool_name": "Edit",
        "cwd": str(repo),
        "tool_input": {"file_path": str(repo / path)},
    }


def _decision(hook: dict, repo: Path, path: str, capsys, *, args=None, **kwargs) -> str | None:
    assert hook["_pretooluse"](_event(repo, path, **kwargs), repo, args or _args()) == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None


def _counting(hook: dict) -> list[str]:
    calls: list[str] = []
    original = hook["_org_policy_packs"]

    def counted(root, config, data):
        calls.append(config)
        return original(root, config, data)

    hook["_org_policy_packs"] = counted
    return calls


def test_the_hook_asks_before_an_edit_to_a_declared_pack(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    repo = _repo(tmp_path, VENDORED_PACK)
    hook = _hook()

    hook["_pretooluse"](_event(repo, VENDORED_PACK), repo, _args())
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]

    assert output["permissionDecision"] == "ask"
    assert (
        f"{VENDORED_PACK} (protected policy surface, pattern checks.policy_packs in shipgate.yaml)"
        in output["permissionDecisionReason"]
    )
    assert _decision(hook, repo, "src/app.py", capsys) is None
    assert _decision(hook, repo, "policies/org.yaml", capsys) == "ask"
    # An operator's hard block covers a declared pack too.
    monkeypatch.setenv("AGENTS_SHIPGATE_PRETOOLUSE_DECISION", "deny")
    assert _decision(hook, repo, VENDORED_PACK, capsys) == "deny"


def test_the_hook_asks_for_a_pack_only_the_base_declares(tmp_path: Path, capsys) -> None:
    """Un-declaring the pack in the working tree does not unprotect it."""

    repo = _repo(tmp_path, VENDORED_PACK)
    (repo / "shipgate.yaml").write_text(
        (SAMPLE / "shipgate.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    hook = _hook()

    assert _decision(hook, repo, VENDORED_PACK, capsys) == "ask"
    # With no readable base, only the working tree's declarations apply.
    assert _decision(hook, repo, VENDORED_PACK, capsys, args=_args(base="origin/main")) is None


def test_the_hook_asks_for_a_pack_the_working_tree_declares(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    _declare_packs(repo / "shipgate.yaml", "rules/new.yaml")
    hook = _hook()

    assert _decision(hook, repo, "rules/new.yaml", capsys) == "ask"


def test_the_hook_parses_a_manifest_only_when_it_can_name_a_pack(tmp_path: Path, capsys) -> None:
    """No ``policy_packs`` and no escape: no CLI call on an ordinary edit."""

    repo = _repo(tmp_path)
    hook = _hook()
    calls = _counting(hook)

    assert _decision(hook, repo, "src/app.py", capsys) is None
    assert calls == []


def test_the_hook_reads_each_manifest_version_once(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path, VENDORED_PACK)
    hook = _hook()
    calls = _counting(hook)

    assert _decision(hook, repo, VENDORED_PACK, capsys) == "ask"
    assert _decision(hook, repo, "src/app.py", capsys) is None
    assert _decision(hook, repo, VENDORED_PACK, capsys, session="S2") == "ask"
    assert calls == ["shipgate.yaml"]
    # A new manifest version is read once more; the base's is still remembered.
    _declare_packs(repo / "shipgate.yaml", "rules/new.yaml")
    assert _decision(hook, repo, "rules/new.yaml", capsys) == "ask"
    assert calls == ["shipgate.yaml", "shipgate.yaml"]


def test_a_manifest_the_cli_cannot_read_protects_the_files_it_names(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    manifest = repo / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\nchecks:\n  policy_packs: [rules/broken.yaml\n",
        encoding="utf-8",
    )
    hook = _hook()

    assert _decision(hook, repo, "rules/broken.yaml", capsys) == "ask"
    assert _decision(hook, repo, "src/app.py", capsys) is None


def test_a_cli_that_does_not_answer_is_asked_again(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path, VENDORED_PACK)
    hook = _hook()
    hook["_cli"] = lambda: [str(tmp_path / "missing-shipgate")]

    # Nothing parsed the manifest, so only the name it spells is protected.
    assert _decision(hook, repo, VENDORED_PACK, capsys) == "ask"
    assert _decision(hook, repo, "src/app.py", capsys) is None
    hook["_cli"] = lambda: [str(LAUNCHER)]
    calls = _counting(hook)
    assert _decision(hook, repo, VENDORED_PACK, capsys) == "ask"
    assert calls == ["shipgate.yaml"]


def test_an_approved_pack_edit_is_remembered_for_the_session(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path, VENDORED_PACK)
    hook = _hook()
    hook["_run_trigger_for_paths"] = lambda *_args, **_kwargs: None

    assert _decision(hook, repo, VENDORED_PACK, capsys) == "ask"
    _write_pack(repo / VENDORED_PACK, version="2")
    hook["_trigger"](_event(repo, VENDORED_PACK), repo, _args())
    capsys.readouterr()

    assert _decision(hook, repo, VENDORED_PACK, capsys) is None
    assert _decision(hook, repo, VENDORED_PACK, capsys, session="S2") == "ask"
