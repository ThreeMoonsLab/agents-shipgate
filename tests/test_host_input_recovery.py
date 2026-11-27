"""#547: actual failed reads stay denied and explain the observed obligation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.agent_boundary import (
    _sanitize_violations,
    build_agent_boundary_result,
    evaluate_agent_boundary,
)
from agents_shipgate.core.boundary_diff import BoundaryInputIssue
from agents_shipgate.core.codex_boundary import violations_within_agent_actionable_band
from agents_shipgate.core.host_grants import (
    HostBoundarySnapshot,
    HostStaticParseCache,
    build_host_boundary_snapshot,
    host_audit_inventory,
)
from agents_shipgate.core.host_input_failure import MAX_FAILURE_TEXT
from agents_shipgate.core.trust_roots import IdentityBoundReadSession
from agents_shipgate.schemas.agent_result_v1 import AgentResultViolatedRule


def _result(workspace: Path, snapshot: HostBoundarySnapshot):
    return build_agent_boundary_result(evaluate_agent_boundary(
        workspace=workspace, diff_text="", host_snapshot=snapshot, input_mode="worktree",
        verification_replayable=True,
    )).model_dump(mode="json")


def _denied(payload: dict) -> list[dict]:
    assert payload["input_coverage"] == "partial"
    assert payload["control"]["state"] == "human_review_required"
    assert not any(payload["control"]["permissions"].values())
    assert payload["control"]["allowed_next_commands"] == []
    return [v for v in payload["violations"] if v["evidence"].get("recovery")]


def test_directory_error_is_not_misclassified_as_resource_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "project").mkdir()
    original = IdentityBoundReadSession.directory_entries

    def unreadable(self, relative=Path(), **kwargs):
        if relative == Path("project"):
            raise PermissionError("arbitrary private filesystem error")
        return original(self, relative, **kwargs)

    monkeypatch.setattr(IdentityBoundReadSession, "directory_entries", unreadable)
    snapshot = build_host_boundary_snapshot(tmp_path)
    payload = _result(tmp_path, snapshot)
    rows = _denied(payload)
    assert len(rows) == 1  # shared failure, not one repair per host
    recovery = rows[0]["evidence"]["recovery"]
    assert recovery == {
        "reason": "input_unreadable", "phase": "inventory_enumeration", "source": "project",
    }
    assert "project" in payload["control"]["next_action"]["why"]
    assert "Directory inventory" in payload["control"]["reason"]
    assert "resource" not in rows[0]["title"]
    assert snapshot.inventory["artifacts"] == snapshot.inventory["grants"] == []


@pytest.mark.parametrize("bound", ["entries", "bytes", "repository_entries"])
def test_real_resource_limits_are_visible_without_granting_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str,
) -> None:
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}')
    cache = HostStaticParseCache(
        max_entries=1 if bound == "entries" else 10_000,
        max_total_bytes=1 if bound == "bytes" else 1_000,
    )
    if bound == "repository_entries":
        monkeypatch.setattr("agents_shipgate.core.host_grants.MAX_HOST_REPOSITORY_ENTRIES", 0)
    snapshot = build_host_boundary_snapshot(tmp_path, cache=cache)
    rows = _denied(_result(tmp_path, snapshot))
    assert snapshot.inventory["artifacts"] == snapshot.inventory["grants"] == []
    issue_ids = [row["issue_id"] for row in snapshot.inventory["issues"]]
    assert len(issue_ids) == len(set(issue_ids))
    known = [v["evidence"]["recovery"] for v in rows]
    assert all(r["reason"] == "resource_bound_exceeded" for r in known)
    key = {"entries": "aggregate_entries", "bytes": "aggregate_bytes"}.get(bound, bound)
    expected = 0 if bound == "repository_entries" else 1
    assert all(r["configured_limits"][key] == expected for r in known)
    assert any(f"{key}={expected}" in v["title"] for v in rows)
    if bound == "bytes":
        assert all(r["source"] == ".mcp.json" for r in known)


@pytest.mark.parametrize("failure_kind", ["read", "decode"])
def test_named_source_failures_keep_their_own_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str,
) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text("{}")
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}')
    original = IdentityBoundReadSession.read_bytes

    def read(self, relative, **kwargs):
        if relative.as_posix() == ".claude/settings.json":
            if failure_kind == "decode":
                return b"\xff"
            # The word 'changed' is deliberately not evidence of snapshot drift.
            raise ValueError("changed permission: private error text")
        return original(self, relative, **kwargs)

    monkeypatch.setattr(IdentityBoundReadSession, "read_bytes", read)
    snapshot = build_host_boundary_snapshot(tmp_path)
    rows = _denied(_result(tmp_path, snapshot))
    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == ".claude/settings.json"
    assert row["evidence"]["recovery"]["source"] == row["path"]
    assert row["evidence"]["recovery"]["reason"] == "input_unreadable"
    assert row["evidence"]["recovery"]["phase"] == (
        "utf8_decode" if failure_kind == "decode" else "source_read"
    )
    assert "private error text" not in json.dumps(snapshot.inventory)
    assert row["path"] in row["recommendation"]
    assert all(count == 1 for count in snapshot.cache.read_counts.values())
    assert all(count == 1 for count in snapshot.cache.parse_counts.values())


@pytest.mark.parametrize("failure_kind", ["value_error", "os_error"])
def test_final_validation_does_not_guess_why_coherent_read_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str,
) -> None:
    source = tmp_path / ".mcp.json"
    source.write_text('{"mcpServers": {}}')

    def finish(self):
        # An untyped failure: nothing names an entry or an observation, so
        # nothing is published beyond the read root (#588).
        if failure_kind == "os_error":
            raise OSError("unclassified failure with sensitive context")
        raise ValueError("unclassified failure with sensitive context")

    monkeypatch.setattr(IdentityBoundReadSession, "finish", finish)
    snapshot = build_host_boundary_snapshot(tmp_path)
    payload = _result(tmp_path, snapshot)
    rows = _denied(payload)
    assert len(rows) == 1
    assert rows[0]["evidence"]["recovery"] == {
        "reason": "snapshot_validation_failed", "phase": "snapshot_validation", "source": ".",
    }
    assert "could not confirm a coherent read" in payload["control"]["reason"]
    assert "did not establish whether concurrent changes" in rows[0]["recommendation"]
    assert snapshot.inventory["artifacts"] == snapshot.inventory["grants"] == []
    assert "sensitive context" not in json.dumps(snapshot.inventory)


def _changed_during_final_validation(
    monkeypatch: pytest.MonkeyPatch, source: Path,
) -> None:
    """A real change to ``source`` after it was read, before final validation."""

    original = IdentityBoundReadSession.finish

    def finish(self):
        source.write_text('{"mcpServers": {}, "changed": true}')
        return original(self)

    monkeypatch.setattr(IdentityBoundReadSession, "finish", finish)


def test_final_validation_names_the_entry_whose_change_it_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#588: the typed reader fact reaches the recovery, not parsed prose."""

    import jsonschema

    source = tmp_path / ".mcp.json"
    source.write_text('{"mcpServers": {}}')
    _changed_during_final_validation(monkeypatch, source)
    snapshot = build_host_boundary_snapshot(tmp_path)
    payload = _result(tmp_path, snapshot)
    rows = _denied(payload)
    assert len(rows) == 1
    assert rows[0]["evidence"]["recovery"] == {
        "reason": "snapshot_validation_failed", "phase": "snapshot_validation", "source": ".",
        "entry": ".mcp.json", "observation": "identity_mismatch",
    }
    # The failure still discards every read under the root: the entry locates
    # it, and the issue keeps the root as its source, so no comparison or
    # routing rule can treat it as one path's limit.
    assert rows[0]["path"] == "."
    assert {(i["kind"], i["source"]) for i in snapshot.inventory["issues"]} == {
        ("unreadable", "."),
    }
    assert snapshot.inventory["artifacts"] == snapshot.inventory["grants"] == []
    assert "could not confirm a coherent read" in payload["control"]["reason"]
    assert ".mcp.json no longer had the identity it was read with" in rows[0]["title"]
    assert "observed that .mcp.json differed" in rows[0]["recommendation"]
    assert all(count == 1 for count in snapshot.cache.read_counts.values())
    for name, document in (
        ("agent-boundary-result-schema.v3.json", payload),
        ("host-grants-inventory-schema.v0.10.json", snapshot.inventory),
    ):
        jsonschema.validate(document, json.loads((Path("docs") / name).read_text()))


def test_final_validation_does_not_claim_an_uninspectable_entry_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}')
    original_finish = IdentityBoundReadSession.finish
    original_lstat = Path.lstat

    def lstat(self):
        if self.name == ".mcp.json":
            raise PermissionError("private filesystem detail")
        return original_lstat(self)

    def finish(self):
        # Only the final pass cannot look; the read itself succeeded.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path, "lstat", lstat)
            return original_finish(self)

    monkeypatch.setattr(IdentityBoundReadSession, "finish", finish)
    snapshot = build_host_boundary_snapshot(tmp_path)
    payload = _result(tmp_path, snapshot)
    rows = _denied(payload)
    assert len(rows) == 1
    assert rows[0]["evidence"]["recovery"] == {
        "reason": "snapshot_validation_failed", "phase": "snapshot_validation", "source": ".",
        "entry": ".mcp.json", "observation": "inspection_unavailable",
    }
    assert snapshot.inventory["artifacts"] == snapshot.inventory["grants"] == []
    published = json.dumps(payload) + json.dumps(snapshot.inventory)
    assert "private filesystem detail" not in published
    for text in (rows[0]["title"], rows[0]["recommendation"], payload["control"]["reason"]):
        assert "no longer" not in text
        assert "differed" not in text
    assert ".mcp.json could not be inspected" in rows[0]["title"]
    assert "did not establish that it changed" in rows[0]["recommendation"]


def test_final_validation_entry_is_bounded_and_redacted_but_kept_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "ghp_" + "S" * 36
    folder = "settings TOKEN=" + secret + " " + "long" * 20
    (tmp_path / folder).mkdir()
    source = tmp_path / folder / ".mcp.json"
    source.write_text('{"mcpServers": {}}')
    _changed_during_final_validation(monkeypatch, source)
    snapshot = build_host_boundary_snapshot(tmp_path)
    payload = _result(tmp_path, snapshot)
    rows = _denied(payload)
    assert len(rows) == 1
    entry = rows[0]["evidence"]["recovery"]["entry"]
    assert rows[0]["evidence"]["recovery"]["observation"] == "identity_mismatch"
    assert entry.startswith("settings TOKEN=") and entry.endswith("/.mcp.json")
    assert len(entry) <= MAX_FAILURE_TEXT
    assert "\n" not in entry
    for published in (payload, snapshot.inventory):
        assert secret not in json.dumps(published)
    # The private failure keeps the exact spelling the reader held: locating
    # the original entry needs no second filesystem read.
    assert {failure.entry for failure in snapshot.input_failures.values()} == {
        f"{folder}/.mcp.json",
    }


def test_old_snapshot_has_no_invented_recovery_metadata(tmp_path: Path, monkeypatch) -> None:
    def fail(self):
        raise ValueError("unclassified")

    monkeypatch.setattr(IdentityBoundReadSession, "finish", fail)
    current = build_host_boundary_snapshot(tmp_path)
    old = HostBoundarySnapshot(current.inventory, current.cache)
    payload = _result(tmp_path, old)
    assert _denied(payload) == []
    assert all("recovery" not in v["evidence"] for v in payload["violations"])


def test_failure_then_explicit_valid_run_replaces_stop_identity(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}')
    original = IdentityBoundReadSession.finish
    calls = 0

    def fail_once(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("transient failure, no known cause")
        return original(self)

    monkeypatch.setattr(IdentityBoundReadSession, "finish", fail_once)
    failed = _result(tmp_path, build_host_boundary_snapshot(tmp_path))
    _denied(failed)
    assert calls == 1  # never silently retry into a new authority
    valid = _result(tmp_path, build_host_boundary_snapshot(tmp_path))
    assert valid["input_coverage"] == "complete"
    assert valid["control"]["state"] == "complete"
    assert failed["audit_id"] != valid["audit_id"]
    assert failed["control"]["state"] == "human_review_required"


@pytest.mark.parametrize("output", ["boundary", "control", "audit"])
def test_public_cli_recovery_is_bounded_redacted_and_locatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str,
) -> None:
    secret = "ghp_" + "S" * 36
    folder = "settings TOKEN=" + secret + " " + "long" * 20
    (tmp_path / folder).mkdir()
    original = IdentityBoundReadSession.directory_entries

    def fail(self, relative=Path(), **kwargs):
        if relative.as_posix() == folder:
            raise OSError(f"{secret} https://user:password@host/private?token=SECRET " + "x" * 5000)
        return original(self, relative, **kwargs)

    monkeypatch.setattr(IdentityBoundReadSession, "directory_entries", fail)
    args = ["audit", "--host", "--json"] if output == "audit" else [
        "check", "--format", "agent-control-json" if output == "control" else "agent-boundary-json",
    ]
    if output != "audit":
        diff = tmp_path / "empty.diff"
        diff.write_text("")
        args.extend(["--diff", str(diff)])
    invoked = CliRunner().invoke(app, [*args, "--workspace", str(tmp_path)])
    assert invoked.exit_code == 0, invoked.output
    assert secret not in invoked.output
    assert "user:password" not in invoked.output
    assert "private?token" not in invoked.output
    assert "x" * 100 not in invoked.output
    payload = json.loads(invoked.output)
    if output == "boundary":
        rows = _denied(payload)
        assert len(rows) == 1
        assert rows[0]["path"].startswith("settings TOKEN=")
        assert len(rows[0]["path"]) <= MAX_FAILURE_TEXT
        assert "\n" not in rows[0]["path"]
        assert "settings TOKEN=" in payload["control"]["next_action"]["why"]
    elif output == "control":
        assert payload["control_state"] == "human_review_required"
        assert not any(payload["permissions"].values())
        assert "settings TOKEN=" in payload["reason"]
    else:
        assert payload["issues"]
        assert all(len(i["message"]) <= MAX_FAILURE_TEXT for i in payload["issues"])
        assert all("\n" not in i["source"] for i in payload["issues"])


def test_multiple_read_failures_are_associated_by_issue_identity(tmp_path: Path) -> None:
    for folder in ("pkg a", "pkg b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / ".mcp.json").write_bytes(b"\xff")
    snapshot = build_host_boundary_snapshot(tmp_path)
    payload = _result(tmp_path, snapshot)
    rows = _denied(payload)
    assert {r["path"] for r in rows} == {"pkg a/.mcp.json", "pkg b/.mcp.json"}
    assert all(r["evidence"]["recovery"]["source"] == r["path"] for r in rows)
    assert all(r["path"] in r["recommendation"] for r in rows)
    assert _result(tmp_path, snapshot) == payload
    assert host_audit_inventory(tmp_path, snapshot=snapshot) == snapshot.inventory


def test_fallback_rows_are_sanitized_before_dedupe_and_identity(tmp_path: Path) -> None:
    def build(secret: str):
        return build_agent_boundary_result(evaluate_agent_boundary(
            workspace=tmp_path, diff_text="", input_issues=[
                BoundaryInputIssue(
                    code="host_inventory_unreadable",
                    path=f"TOKEN={secret}\n" + "long-path/" * 100,
                    message=f"TOKEN={secret}\n" + "large-error " * 100,
                ),
            ],
        )).model_dump(mode="json")

    first = build("ghp_" + "A" * 36)
    second = build("ghp_" + "B" * 36)
    assert first == second  # redacted private values do not affect control identity
    _denied(first)
    for row in first["violations"]:
        assert len(row["path"]) <= MAX_FAILURE_TEXT
        assert "\n" not in row["path"]
    for row in first["diagnostics"]:
        assert len(row["message"]) <= MAX_FAILURE_TEXT


def test_recovery_path_bounding_cannot_soften_other_rows_trust_root_classification() -> None:
    row = AgentResultViolatedRule(
        id="BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED",
        check_id="SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED",
        action="require_review", risk_level="medium",
        title="Instruction change", path="long-directory/" * 40 + "AGENTS.md",
        evidence={}, recommendation="Review the instruction change.",
    )
    sanitized = _sanitize_violations([row])
    assert sanitized[0].path == row.path
    assert not violations_within_agent_actionable_band([row])
    assert not violations_within_agent_actionable_band(sanitized)
    assert violations_within_agent_actionable_band([
        row.model_copy(update={"path": row.path[:MAX_FAILURE_TEXT]}),
    ])  # losing the basename really would remove this row's gate classification


def test_existing_closed_schemas_accept_recovery_projection(tmp_path: Path, monkeypatch) -> None:
    import jsonschema

    def fail(self):
        raise ValueError("no coherent read")

    monkeypatch.setattr(IdentityBoundReadSession, "finish", fail)
    snapshot = build_host_boundary_snapshot(tmp_path)
    for name, payload in (
        ("agent-boundary-result-schema.v3.json", _result(tmp_path, snapshot)),
        ("host-grants-inventory-schema.v0.10.json", snapshot.inventory),
    ):
        jsonschema.validate(payload, json.loads((Path("docs") / name).read_text()))
