from __future__ import annotations

import base64
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from typer.testing import CliRunner

from agents_shipgate.cli import authorization as authorization_cli
from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify import orchestrator
from agents_shipgate.cli.verify.orchestrator import run_verify
from agents_shipgate.core import authorization_execution
from agents_shipgate.core.authorization_execution import authorization_execute_command
from agents_shipgate.core.human_authorization import (
    evaluate_human_authorization,
    human_authorization_signature_payload,
)
from agents_shipgate.core.verification_identity import (
    build_terminal_receipt,
    sha256_file,
    validate_receipt_artifacts,
)
from agents_shipgate.schemas.human_authorization import (
    HumanAuthorizationPrincipalV1,
    HumanAuthorizationProofV1,
    HumanAuthorizationRequestV1,
    HumanAuthorizationStatementV1,
    HumanAuthorizationV1,
    TrustedEd25519KeyV1,
    authorization_review_items,
    build_git_push_operation,
    build_human_authorization,
    build_human_authorization_request,
    build_human_authorization_trust_policy,
    ed25519_key_id,
)
from agents_shipgate.schemas.verification_identity import (
    VerificationInputSet,
    VerificationPlan,
    VerificationReceipt,
    VerificationUnitResult,
    content_id,
)

DESTINATION_REF = "refs/heads/codex/human-authorization-state"
LEASE_OID = "e" * 40
REPOSITORY_ID = "example.test/acme/review-agent"
PUSH_URL = "https://example.test/acme/review-agent.git"
runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Authorization Test",
            "GIT_AUTHOR_EMAIL": "authorization@example.test",
            "GIT_COMMITTER_NAME": "Authorization Test",
            "GIT_COMMITTER_EMAIL": "authorization@example.test",
        },
    )


def _committed_review_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "shipgate.yaml").write_text(
        """
version: "0.1"
project:
  name: authorization-integration
agent:
  name: empty-test-agent
  declared_purpose:
    - exercise verifier authorization integration
environment:
  target: local
tool_sources:
  - id: docs_tools
    type: mcp
    path: tools.json
agent_bindings:
  declarations:
    - agent: root
      complete: true
      tools:
        - {tool: docs.lookup, source_id: docs_tools}
      handoffs: []
      reason: reviewed local integration-test binding
permissions:
  scopes:
    - docs:read
action_surface:
  actions:
    - tool: docs.lookup
      effect: read
      scopes: [docs:read]
      authority:
        mode: scoped
        auth_type: oauth2
        credential_mode: delegated
""".lstrip(),
        encoding="utf-8",
    )
    (repo / "tools.json").write_text(
        """
{
  "tools": [
    {
      "name": "docs.lookup",
      "description": "Look up metadata for one existing documentation article.",
      "inputSchema": {
        "type": "object",
        "properties": {"article_id": {"type": "string"}},
        "required": ["article_id"],
        "additionalProperties": false
      },
      "annotations": {"readOnlyHint": true},
      "auth": {"type": "oauth2", "scopes": ["docs:read"]},
      "owner": "docs-platform"
    }
  ]
}
""".lstrip(),
        encoding="utf-8",
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", "https://example.test/acme/review-agent.git")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")

    tools_path = repo / "tools.json"
    tools_path.write_text(
        tools_path.read_text(encoding="utf-8").replace(
            "Look up metadata for one existing documentation article.",
            "Too short.",
        ),
        encoding="utf-8",
    )
    _git(repo, "add", "tools.json")
    _git(repo, "commit", "-q", "-m", "introduce review item")
    return repo


def _verify(
    repo: Path,
    *,
    authorization: Path | None = None,
    plugins_enabled: bool = False,
):
    return run_verify(
        workspace=repo,
        config=Path("shipgate.yaml"),
        base="HEAD~1",
        head="HEAD",
        archive_head=True,
        out=repo / "agents-shipgate-reports",
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=plugins_enabled,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        verbose=False,
        authorization=authorization,
    )


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _request_from_first_verification(repo: Path) -> HumanAuthorizationRequestV1:
    out = repo / "agents-shipgate-reports"
    receipt = _validated_receipt(out)
    plan = VerificationPlan.model_validate(_load_json(out / "verification-plan.json"))
    report = _load_json(out / "report.json")
    release_decision = report["release_decision"]
    assert isinstance(release_decision, dict)
    review_items = authorization_review_items(release_decision)
    git = plan.subject.git
    assert git.base_commit_sha is not None
    assert git.merge_base_sha is not None
    assert git.base_tree_sha is not None
    assert git.head_tree_sha is not None
    assert git.source_head_commit_sha is not None
    assert git.repository_id == REPOSITORY_ID
    operation = build_git_push_operation(
        destination_repository_id=git.repository_id,
        push_url=PUSH_URL,
        source_commit_sha=git.source_head_commit_sha,
        destination_ref=DESTINATION_REF,
        expected_lease_oid=LEASE_OID,
    )
    verifier = _load_json(out / "verifier.json")
    decision_id = verifier["decision_id"]
    assert isinstance(decision_id, str)
    return build_human_authorization_request(
        repository_id=git.repository_id,
        source_receipt_id=receipt.receipt_id,
        source_artifact_set_id=receipt.artifact_set_id,
        source_engine_requirement_id=receipt.engine_requirement_id,
        source_executor_id=receipt.executor_id,
        verification_request_id=plan.request_id,
        subject_id=plan.subject.subject_id,
        decision_id=decision_id,
        base_commit_sha=git.base_commit_sha,
        merge_base_sha=git.merge_base_sha,
        base_tree_sha=git.base_tree_sha,
        head_tree_sha=git.head_tree_sha,
        source_head_commit_sha=git.source_head_commit_sha,
        review_items=review_items,
        operation=operation,
    )


def _trusted_key(
    private_key: Ed25519PrivateKey,
    *,
    now: datetime,
) -> TrustedEd25519KeyV1:
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return TrustedEd25519KeyV1(
        key_id=ed25519_key_id(public_key),
        public_key=_b64url(public_key),
        provider="github",
        principal="github:user:release-reviewer",
        valid_from=now - timedelta(days=1),
        valid_until=now + timedelta(days=1),
    )


def _signed_grant(
    private_key: Ed25519PrivateKey,
    request: HumanAuthorizationRequestV1,
    *,
    issued_at: datetime,
    not_before: datetime,
    expires_at: datetime,
) -> HumanAuthorizationV1:
    statement = HumanAuthorizationStatementV1(
        request=request,
        principal=HumanAuthorizationPrincipalV1(
            provider="github",
            subject="github:user:release-reviewer",
        ),
        reason="Reviewed the exact force-with-lease operation.",
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        nonce=_b64url(b"authorization-e2e-nonce"),
    )
    proof = HumanAuthorizationProofV1(
        key_id=ed25519_key_id(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ),
        signature=_b64url(private_key.sign(human_authorization_signature_payload(statement))),
    )
    return build_human_authorization(statement=statement, proof=proof)


def _write_host_policy(
    path: Path,
    *,
    request: HumanAuthorizationRequestV1,
    key: TrustedEd25519KeyV1,
) -> None:
    policy = build_human_authorization_trust_policy(
        repository_ids=[request.repository_id],
        keys=[key],
        max_ttl_seconds=900,
        clock_skew_seconds=30,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    path.chmod(0o600)


def _write_grant(path: Path, grant: HumanAuthorizationV1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(grant.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _validated_receipt(out: Path) -> VerificationReceipt:
    receipt = VerificationReceipt.model_validate(_load_json(out / "verification-receipt.json"))
    validate_receipt_artifacts(receipt, root=out)
    return receipt


def _static_gate(verifier) -> tuple[str | None, str, bool]:
    return verifier.decision, verifier.merge_verdict, verifier.can_merge_without_human


def _install_protected_test_broker_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model the externally protected launcher required by the broker contract."""

    launcher = tmp_path / "host-runtime" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    launcher.chmod(0o555)
    monkeypatch.setattr(
        authorization_execution,
        "_authorization_python_launcher",
        lambda: launcher,
    )


def test_signed_authorization_overlays_one_exact_command_and_binds_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _committed_review_repo(tmp_path)
    initial, report, exit_code = _verify(repo)
    assert exit_code == 0
    assert report is not None
    assert _static_gate(initial) == (
        "review_required",
        "human_review_required",
        False,
    )
    _validated_receipt(repo / "agents-shipgate-reports")

    request = _request_from_first_verification(repo)
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    trust_path = tmp_path / "host" / "trust-policy.json"
    _write_host_policy(
        trust_path,
        request=request,
        key=_trusted_key(private_key, now=now),
    )
    grant = _signed_grant(
        private_key,
        request,
        issued_at=now - timedelta(seconds=1),
        not_before=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5),
    )
    grant_path = tmp_path / "host" / "authorization.json"
    _write_grant(grant_path, grant)
    monkeypatch.setattr(
        orchestrator,
        "default_human_authorization_trust_policy_path",
        lambda: trust_path,
    )
    _install_protected_test_broker_runtime(tmp_path, monkeypatch)

    authorized, authorized_report, authorized_exit = _verify(
        repo,
        authorization=grant_path,
    )

    assert authorized_exit == exit_code
    assert authorized_report is not None
    assert _static_gate(authorized) == _static_gate(initial)
    assert authorized_report.release_decision.decision == report.release_decision.decision
    assert authorized.authorization.status == "accepted"
    assert authorized.authorization.authorization_id == grant.authorization_id
    out = repo / "agents-shipgate-reports"
    execute_command = authorization_execute_command(
        workspace=repo,
        receipt=out / "verification-receipt.json",
        artifacts_root=out,
    )
    assert authorized.authorization.command == execute_command
    assert authorized.control.state == "agent_action_required"
    assert authorized.control.must_stop is False
    assert authorized.control.completion_allowed is False
    assert authorized.control.allowed_next_commands == [execute_command]
    assert authorized.control.next_action.kind == "repair"
    assert authorized.control.next_action.command == execute_command
    assert authorized.fix_task is None

    handoff = _load_json(out / "agent-handoff.json")
    assert handoff["authorization"]["status"] == "accepted"  # type: ignore[index]
    assert handoff["control"] == authorized.control.model_dump(mode="json")
    assert handoff["gate"]["decision"] == "review_required"  # type: ignore[index]
    assert handoff["gate"]["merge_verdict"] == "human_review_required"  # type: ignore[index]
    assert handoff["gate"]["can_merge_without_human"] is False  # type: ignore[index]

    canonical_path = out / "human-authorization.json"
    assert _load_json(canonical_path) == grant.model_dump(mode="json")
    assert authorized.artifacts["human_authorization_json"] == (
        "agents-shipgate-reports/human-authorization.json"
    )
    receipt = _validated_receipt(out)
    assert receipt.decision == "review_required"
    assert receipt.merge_verdict == "human_review_required"
    assert receipt.can_merge_without_human is False
    assert "human_authorization_json" in receipt.artifact_manifest.artifacts

    # A broker must not touch promised/missing source objects through the
    # workspace's agent-controlled partial-clone configuration before entering
    # the hardened executor. Such a read could otherwise launch remote-ext.
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    loose_tree = repo / ".git" / "objects" / tree[:2] / tree[2:]
    assert loose_tree.is_file()
    loose_tree.unlink()
    helper_marker = tmp_path / "pre-executor-remote-helper-ran"
    _git(repo, "config", "core.repositoryformatversion", "1")
    _git(repo, "config", "extensions.partialClone", "evil")
    _git(repo, "config", "remote.evil.promisor", "true")
    _git(repo, "config", "remote.evil.partialclonefilter", "tree:0")
    _git(repo, "config", "protocol.ext.allow", "always")
    _git(repo, "config", "remote.evil.url", f"ext::touch {helper_marker}")

    executed: dict[str, object] = {}

    def _execute(
        operation,
        *,
        workspace,
        expected_source_tree_sha,
        revalidate_authority,
    ):
        revalidate_authority()
        executed["operation"] = operation
        executed["workspace"] = workspace
        executed["expected_source_tree_sha"] = expected_source_tree_sha
        return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        authorization_cli,
        "default_human_authorization_trust_policy_path",
        lambda: trust_path,
    )
    monkeypatch.setattr(authorization_cli, "execute_pinned_git_push", _execute)

    # A receipt that claims an enabled plugin engine must fail before the
    # protected broker inspects the workspace or validates a plugin catalog.
    plan = VerificationPlan.model_validate(_load_json(out / "verification-plan.json"))
    plugin_plan_payload = plan.model_dump(mode="json")
    plugin_inputs_payload = plan.inputs.model_dump(mode="json")
    plugin_inputs_payload["options"]["plugins_enabled"] = True
    plugin_inputs_payload["input_set_id"] = content_id(
        {
            key: value
            for key, value in plugin_inputs_payload.items()
            if key != "input_set_id"
        }
    )
    plugin_inputs = VerificationInputSet.model_validate(plugin_inputs_payload)
    plugin_plan_payload["inputs"] = plugin_inputs.model_dump(mode="json")
    plugin_plan_payload["request_id"] = content_id(
        {
            "subject_id": plan.subject.subject_id,
            "input_set_id": plugin_inputs.input_set_id,
            "engine_requirement_id": plan.engine.engine_requirement_id,
            "task_ids": [task.task_id for task in plan.tasks],
        }
    )
    plugin_plan = VerificationPlan.model_validate(plugin_plan_payload)
    plugin_artifacts = {
        name: (out / ref.path).read_bytes()
        for name, ref in receipt.artifact_manifest.artifacts.items()
    }
    plugin_artifacts["verification_plan_json"] = plugin_plan.model_dump_json().encode()

    with monkeypatch.context() as plugin_guard:
        plugin_guard.setattr(
            authorization_cli,
            "load_validated_receipt_artifacts",
            lambda **_kwargs: (receipt, plugin_artifacts),
        )
        plugin_guard.setattr(
            authorization_cli,
            "authorization_workspace_root",
            lambda _workspace: pytest.fail("plugin guard ran after protected runtime entry"),
        )
        plugin_result = runner.invoke(
            app,
            [
                "authorization",
                "execute",
                "--workspace",
                str(repo),
                "--receipt",
                str(out / "verification-receipt.json"),
                "--artifacts-root",
                str(out),
            ],
        )
    assert plugin_result.exit_code == 3
    assert "plugins-disabled engine mode" in plugin_result.output
    assert executed == {}

    execute_result = runner.invoke(
        app,
        [
            "authorization",
            "execute",
            "--workspace",
            str(repo),
            "--receipt",
            str(out / "verification-receipt.json"),
            "--artifacts-root",
            str(out),
        ],
    )
    assert execute_result.exit_code == 0, execute_result.output
    assert not helper_marker.exists()
    assert json.loads(execute_result.output)["executed"] is True
    assert executed == {
        "operation": request.operation,
        "workspace": repo.resolve(),
        "expected_source_tree_sha": request.head_tree_sha,
    }

    executed.clear()

    # Terminal receipts are content-addressed closure, not an authority
    # signature.  Even when an agent rebuilds that closure around a malicious
    # shell suffix, the executor must derive the wrapper independently and
    # reject the artifact text before dispatching the signed operation.
    original_artifacts = {
        path: path.read_bytes()
        for path in (
            out / "verifier.json",
            out / "verify-run.json",
            out / "verification-artifacts.json",
            out / "verification-receipt.json",
        )
    }
    forged_command = f"{execute_command} ; touch agent-controlled"
    forged_verifier = _load_json(out / "verifier.json")
    forged_verifier["authorization"]["command"] = forged_command  # type: ignore[index]
    forged_verifier["control"]["allowed_next_commands"] = [forged_command]  # type: ignore[index]
    forged_verifier["control"]["next_action"]["command"] = forged_command  # type: ignore[index]
    (out / "verifier.json").write_text(
        json.dumps(forged_verifier, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    forged_verify_run = _load_json(out / "verify-run.json")
    forged_verify_run["artifacts"]["verifier_json"]["sha256"] = sha256_file(  # type: ignore[index]
        out / "verifier.json"
    ).removeprefix("sha256:")
    (out / "verify-run.json").write_text(
        json.dumps(forged_verify_run, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    plan = VerificationPlan.model_validate(_load_json(out / "verification-plan.json"))
    units = [
        VerificationUnitResult.model_validate(_load_json(out / ref.path))
        for name, ref in receipt.artifact_manifest.artifacts.items()
        if name.startswith("verification_unit_result")
    ]
    forged_manifest, forged_receipt = build_terminal_receipt(
        plan=plan,
        unit_results=units,
        decision=receipt.decision,
        merge_verdict=receipt.merge_verdict,
        can_merge_without_human=receipt.can_merge_without_human,
        artifact_paths={
            name: out / ref.path
            for name, ref in receipt.artifact_manifest.artifacts.items()
        },
        artifact_root=out,
    )
    (out / "verification-artifacts.json").write_text(
        forged_manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (out / "verification-receipt.json").write_text(
        forged_receipt.model_dump_json(indent=2),
        encoding="utf-8",
    )
    validate_receipt_artifacts(forged_receipt, root=out)

    forged_result = runner.invoke(
        app,
        [
            "authorization",
            "execute",
            "--workspace",
            str(repo),
            "--receipt",
            str(out / "verification-receipt.json"),
            "--artifacts-root",
            str(out),
        ],
    )
    assert forged_result.exit_code == 3
    assert "bound verifier authorization disagrees" in forged_result.output
    assert executed == {}
    for path, content in original_artifacts.items():
        path.write_bytes(content)
    validate_receipt_artifacts(receipt, root=out)

    def _evaluate_after_expiry(*args, **kwargs):
        return evaluate_human_authorization(
            *args,
            **kwargs,
            now=grant.statement.expires_at,
        )

    monkeypatch.setattr(
        authorization_cli,
        "evaluate_human_authorization",
        _evaluate_after_expiry,
    )
    expired_result = runner.invoke(
        app,
        [
            "authorization",
            "execute",
            "--workspace",
            str(repo),
            "--receipt",
            str(out / "verification-receipt.json"),
            "--artifacts-root",
            str(out),
        ],
    )
    assert expired_result.exit_code == 3
    assert "authorization_expired" in expired_result.output
    assert executed == {}


def test_plugin_enabled_verification_never_exposes_authorized_command(
    tmp_path: Path,
) -> None:
    repo = _committed_review_repo(tmp_path)
    grant_path = tmp_path / "host" / "authorization.json"

    verifier, report, exit_code = _verify(
        repo,
        authorization=grant_path,
        plugins_enabled=True,
    )

    assert exit_code == 0
    assert report is not None
    assert verifier.authorization.status == "not_applicable"
    assert verifier.authorization.reason_codes == [
        "authorization_requires_plugins_disabled"
    ]
    assert verifier.authorization.command is None
    assert verifier.control.state == "human_review_required"
    assert verifier.control.allowed_next_commands == []


@pytest.mark.parametrize(
    "failure",
    ["tampered", "expired", "wrong_tree", "missing_trust"],
)
def test_invalid_authorization_keeps_human_stop_and_receipt_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    repo = _committed_review_repo(tmp_path)
    initial, report, exit_code = _verify(repo)
    assert exit_code == 0
    assert report is not None
    assert initial.control.state == "human_review_required"
    _validated_receipt(repo / "agents-shipgate-reports")

    request = _request_from_first_verification(repo)
    now = datetime.now(UTC)
    private_key = Ed25519PrivateKey.generate()
    trust_path = tmp_path / "host" / "trust-policy.json"
    if failure != "missing_trust":
        _write_host_policy(
            trust_path,
            request=request,
            key=_trusted_key(private_key, now=now),
        )

    grant_request = request
    if failure == "wrong_tree":
        grant_request = build_human_authorization_request(
            repository_id=request.repository_id,
            source_receipt_id=request.source_receipt_id,
            source_artifact_set_id=request.source_artifact_set_id,
            source_engine_requirement_id=request.source_engine_requirement_id,
            source_executor_id=request.source_executor_id,
            verification_request_id=request.verification_request_id,
            subject_id=request.subject_id,
            decision_id=request.decision_id,
            base_commit_sha=request.base_commit_sha,
            merge_base_sha=request.merge_base_sha,
            base_tree_sha="f" * 40,
            head_tree_sha=request.head_tree_sha,
            source_head_commit_sha=request.source_head_commit_sha,
            review_items=request.review_items,
            operation=request.operation,
        )
    issued_at = now - timedelta(seconds=1)
    not_before = now - timedelta(seconds=1)
    expires_at = now + timedelta(minutes=5)
    if failure == "expired":
        issued_at = now - timedelta(minutes=10)
        not_before = issued_at
        expires_at = now - timedelta(seconds=1)
    grant = _signed_grant(
        private_key,
        grant_request,
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
    )
    if failure == "tampered":
        raw = base64.urlsafe_b64decode(
            grant.proof.signature + "=" * (-len(grant.proof.signature) % 4)
        )
        grant = build_human_authorization(
            statement=grant.statement,
            proof=HumanAuthorizationProofV1(
                key_id=grant.proof.key_id,
                signature=_b64url(bytes([raw[0] ^ 1]) + raw[1:]),
            ),
        )
    grant_path = tmp_path / "host" / "authorization.json"
    _write_grant(grant_path, grant)
    monkeypatch.setattr(
        orchestrator,
        "default_human_authorization_trust_policy_path",
        lambda: trust_path,
    )
    _install_protected_test_broker_runtime(tmp_path, monkeypatch)

    rejected, rejected_report, rejected_exit = _verify(
        repo,
        authorization=grant_path,
    )

    assert rejected_exit == exit_code
    assert rejected_report is not None
    assert _static_gate(rejected) == _static_gate(initial)
    assert rejected_report.release_decision.decision == report.release_decision.decision
    assert rejected.authorization.status == "rejected"
    assert rejected.authorization.command is None
    expected_reasons = {
        "tampered": {"signature_invalid"},
        "expired": {"authorization_expired"},
        "wrong_tree": {
            "authorization_request_id_mismatch",
            "authorization_request_mismatch",
        },
        "missing_trust": {"trust_policy_unavailable"},
    }
    assert expected_reasons[failure] <= set(rejected.authorization.reason_codes)
    assert rejected.control.state == "human_review_required"
    assert rejected.control.must_stop is True
    assert rejected.control.completion_allowed is False
    assert rejected.control.allowed_next_commands == []
    assert getattr(rejected.control.next_action, "command", None) is None

    out = repo / "agents-shipgate-reports"
    handoff = _load_json(out / "agent-handoff.json")
    assert handoff["authorization"]["status"] == "rejected"  # type: ignore[index]
    assert handoff["control"] == rejected.control.model_dump(mode="json")
    assert "human_authorization_json" not in rejected.artifacts
    assert not (out / "human-authorization.json").exists()
    receipt = _validated_receipt(out)
    assert "human_authorization_json" not in receipt.artifact_manifest.artifacts
