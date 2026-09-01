"""Human-authorization request and guarded execution tooling.

These commands never sign or approve anything.  ``request`` projects one
content-addressed challenge for an external host; ``execute`` revalidates a
receipt-bound grant at the current wall clock before invoking its typed action.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from agents_shipgate.cli.verify.git import (
    active_replace_refs,
    ensure_git_workspace,
    resolve_git_push_endpoint,
)
from agents_shipgate.cli.workspace_guard import require_workspace
from agents_shipgate.core.authorization_execution import (
    authorization_execute_command,
    authorization_workspace_root,
    ensure_authorization_runtime_is_external,
    execute_pinned_git_push,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.human_authorization import (
    default_human_authorization_trust_policy_path,
    evaluate_human_authorization,
)
from agents_shipgate.core.verification_identity import (
    load_validated_receipt_artifacts,
    validate_engine_requirement,
)
from agents_shipgate.schemas.capability_attestation import (
    CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY,
)
from agents_shipgate.schemas.diagnostics import NextAction
from agents_shipgate.schemas.human_authorization import (
    AuthorizationEvaluationV1,
    HumanAuthorizationV1,
    authorization_review_items,
    build_git_push_operation,
    build_human_authorization_request,
)
from agents_shipgate.schemas.verification_identity import load_verification_plan
from agents_shipgate.schemas.verifier import VerifierArtifact

authorization_app = typer.Typer(no_args_is_help=True)

#: The artifact names a terminal receipt may bind and still be executable under
#: a signed authorization. Deny-by-default on purpose: a receipt naming
#: something this closure does not know about is refused rather than executed
#: over. It is therefore a second copy of what ``verify`` writes, and
#: ``tests/test_capability_delta_attestation.py`` pins the two together — a new
#: verify artifact that nobody adds here makes every authorized push fail.
_AUTHORIZATION_RECEIPT_ARTIFACTS = frozenset(
    {
        "agent_handoff_json",
        "base_capability_lock_json",
        CAPABILITY_DELTA_ATTESTATION_ARTIFACT_KEY,
        "capability_lock_diff_json",
        "capability_lock_diff_markdown",
        "capability_lock_json",
        "human_authorization_json",
        "packet_json",
        "pr_comment",
        "report_json",
        "report_markdown",
        "report_sarif",
        "verification_input_diff",
        "verification_base_report_json",
        "verification_plan_json",
        "verification_unit_result_json",
        "verifier_json",
        "verify_run_json",
    }
)


class _AuthorizedOperationFailed(RuntimeError):
    """The validated operation reached its executor but did not succeed."""


@authorization_app.command("request")
def request_authorization(
    receipt_path: Path = typer.Option(
        Path("agents-shipgate-reports/verification-receipt.json"),
        "--receipt",
        help="Validated terminal receipt for the review-required verification.",
    ),
    artifacts_root: Path = typer.Option(
        Path("agents-shipgate-reports"),
        "--artifacts-root",
        help="Root used to resolve receipt-bound artifacts.",
    ),
    remote: str = typer.Option("origin", "--remote"),
    destination_ref: str = typer.Option(..., "--destination-ref"),
    expected_lease_oid: str = typer.Option(..., "--expected-lease-oid"),
    out: Path = typer.Option(
        Path("agents-shipgate-reports/human-authorization-request.json"),
        "--out",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the JSON result (authorization commands are JSON-native).",
    ),
) -> None:
    """Build an unsigned exact-operation challenge from trusted verifier output."""

    try:
        root = artifacts_root.resolve()
        del json_output
        receipt, artifacts = load_validated_receipt_artifacts(
            receipt_path=receipt_path,
            root=root,
            allowed_artifact_names=_AUTHORIZATION_RECEIPT_ARTIFACTS,
            max_artifact_count=20,
            max_total_size=128 * 1024 * 1024,
        )
        if receipt.decision != "review_required":
            raise ValueError("human authorization requests require decision='review_required'")
        plan = load_verification_plan(
            _load_json_bytes(_required_artifact(artifacts, "verification_plan_json"))
        )
        if plan.inputs.options.get("plugins_enabled") is not False:
            raise ValueError(
                "human authorization requests require an exact plugins-disabled engine mode"
            )
        report = _load_json_bytes(_required_artifact(artifacts, "report_json"))
        release_decision = report.get("release_decision")
        if not isinstance(release_decision, dict):
            raise ValueError("bound report lacks a release_decision object")
        review_items = authorization_review_items(release_decision)
        git = plan.subject.git
        if git.snapshot_kind != "committed_tree" or git.worktree_overlay_sha256 is not None:
            raise ValueError("human authorization rejects uncommitted worktree overlays")
        required_git = {
            "base_commit_sha": git.base_commit_sha,
            "base_tree_sha": git.base_tree_sha,
            "head_tree_sha": git.head_tree_sha,
            "merge_base_sha": git.merge_base_sha,
            "source_head_commit_sha": git.source_head_commit_sha,
        }
        missing = sorted(name for name, value in required_git.items() if value is None)
        if missing:
            raise ValueError(
                "human authorization requires committed PR identity fields: " + ", ".join(missing)
            )
        git_root = ensure_git_workspace(root)
        if active_replace_refs(git_root):
            raise ValueError("human authorization rejects repositories with Git replace refs")
        endpoint = resolve_git_push_endpoint(git_root, remote)
        if endpoint.repository_id != git.repository_id:
            raise ValueError(
                "selected push endpoint repository identity does not match the "
                f"reviewed repository: {endpoint.repository_id!r} != {git.repository_id!r}"
            )
        operation = build_git_push_operation(
            destination_repository_id=endpoint.repository_id,
            push_url=endpoint.push_url,
            source_commit_sha=git.source_head_commit_sha,
            destination_ref=destination_ref,
            expected_lease_oid=expected_lease_oid,
        )
        request = build_human_authorization_request(
            repository_id=git.repository_id,
            source_receipt_id=receipt.receipt_id,
            source_artifact_set_id=receipt.artifact_set_id,
            source_engine_requirement_id=receipt.engine_requirement_id,
            source_executor_id=receipt.executor_id,
            verification_request_id=receipt.request_id,
            subject_id=receipt.subject_id,
            decision_id=receipt.decision_id,
            base_commit_sha=git.base_commit_sha,
            merge_base_sha=git.merge_base_sha,
            base_tree_sha=git.base_tree_sha,
            head_tree_sha=git.head_tree_sha,
            source_head_commit_sha=git.source_head_commit_sha,
            review_items=review_items,
            operation=operation,
        )
        payload = request.model_dump(mode="json")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError as exc:
            guidance = f"Make the authorization request output directory writable: {out.parent}"
            _emit_authorization_error("other_error", exc, guidance)
            raise typer.Exit(4) from exc
        typer.echo(json.dumps({**payload, "output_path": str(out)}, sort_keys=True))
    except (ConfigError, OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        guidance = (
            "Validate a committed review_required verification receipt, then rerun this "
            "command with a safe HTTPS remote, one exact destination ref, and lease OID."
        )
        _emit_authorization_error("input_parse_error", exc, guidance)
        raise typer.Exit(3) from exc


@authorization_app.command("execute")
def execute_authorization(
    workspace: Path = typer.Option(Path("."), "--workspace"),
    receipt_path: Path = typer.Option(
        Path("agents-shipgate-reports/verification-receipt.json"),
        "--receipt",
    ),
    artifacts_root: Path = typer.Option(
        Path("agents-shipgate-reports"),
        "--artifacts-root",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit the JSON result (authorization commands are JSON-native).",
    ),
) -> None:
    """Revalidate and execute one receipt-bound signed operation."""
    require_workspace(workspace)

    try:
        root = artifacts_root.resolve()
        del json_output
        receipt, artifacts = load_validated_receipt_artifacts(
            receipt_path=receipt_path,
            root=root,
            allowed_artifact_names=_AUTHORIZATION_RECEIPT_ARTIFACTS,
            max_artifact_count=20,
            max_total_size=128 * 1024 * 1024,
        )
        if receipt.decision != "review_required":
            raise ValueError("authorized execution requires decision='review_required'")

        plan = load_verification_plan(
            _load_json_bytes(_required_artifact(artifacts, "verification_plan_json"))
        )
        report = _load_json_bytes(_required_artifact(artifacts, "report_json"))
        verifier = VerifierArtifact.model_validate(
            _load_json_bytes(_required_artifact(artifacts, "verifier_json"))
        )
        grant = HumanAuthorizationV1.model_validate(
            _load_json_bytes(_required_artifact(artifacts, "human_authorization_json"))
        )
        release_decision = report.get("release_decision")
        if not isinstance(release_decision, dict):
            raise ValueError("bound report lacks a release_decision object")
        review_items = authorization_review_items(release_decision)

        git = plan.subject.git
        if (
            git.snapshot_kind != "committed_tree"
            or git.worktree_overlay_sha256 is not None
            or git.base_commit_sha is None
            or git.base_tree_sha is None
            or git.head_tree_sha is None
            or git.head_commit_sha is None
            or git.merge_base_sha is None
            or git.source_head_commit_sha is None
            or git.source_head_commit_sha != git.head_commit_sha
        ):
            raise ValueError(
                "authorized execution requires the exact evaluated commit as its source"
            )
        plugins_enabled = plan.inputs.options.get("plugins_enabled")
        if plugins_enabled is not False:
            raise ValueError(
                "authorized execution requires an exact plugins-disabled engine mode"
            )
        git_root = authorization_workspace_root(workspace)
        ensure_authorization_runtime_is_external(git_root)
        validate_engine_requirement(plan.engine, plugins_enabled=plugins_enabled)
        expected_receipt_path = (root / "verification-receipt.json").resolve()
        if receipt_path.resolve() != expected_receipt_path:
            raise ValueError("receipt path must name the terminal receipt in artifacts-root")
        try:
            root.relative_to(git_root)
            expected_receipt_path.relative_to(git_root)
        except ValueError as exc:
            raise ValueError("authorization artifacts must remain inside the workspace") from exc
        if git.repository_id != grant.statement.request.operation.destination_repository_id:
            raise ValueError("signed destination differs from the verified repository")
        signed_source = grant.statement.request
        if signed_source.source_engine_requirement_id != plan.engine.engine_requirement_id:
            raise ValueError("signed source engine differs from the current verified engine")
        if signed_source.source_executor_id != verifier.executor_id:
            raise ValueError("signed source executor differs from the current verified executor")

        # The guarded receipt is the new, authorization-bearing second-pass
        # closure, not the prior receipt from which the signer received its
        # challenge. The source receipt/artifact-set IDs therefore remain
        # signer-authenticated provenance labels. Every operational identity
        # that can be rebuilt from the current closure is checked separately.
        expected_request = build_human_authorization_request(
            repository_id=git.repository_id,
            source_receipt_id=signed_source.source_receipt_id,
            source_artifact_set_id=signed_source.source_artifact_set_id,
            source_engine_requirement_id=signed_source.source_engine_requirement_id,
            source_executor_id=signed_source.source_executor_id,
            verification_request_id=receipt.request_id,
            subject_id=receipt.subject_id,
            decision_id=receipt.decision_id,
            base_commit_sha=git.base_commit_sha,
            merge_base_sha=git.merge_base_sha,
            base_tree_sha=git.base_tree_sha,
            head_tree_sha=git.head_tree_sha,
            source_head_commit_sha=git.source_head_commit_sha,
            review_items=review_items,
            operation=grant.statement.request.operation,
        )
        expected_command = authorization_execute_command(
            workspace=git_root,
            receipt=expected_receipt_path,
            artifacts_root=root,
        )

        def revalidate_authority() -> None:
            """Reload every revocable boundary immediately before push dispatch."""

            current_receipt, current_artifacts = load_validated_receipt_artifacts(
                receipt_path=expected_receipt_path,
                root=root,
                allowed_artifact_names=_AUTHORIZATION_RECEIPT_ARTIFACTS,
                max_artifact_count=20,
                max_total_size=128 * 1024 * 1024,
            )
            if current_receipt != receipt or current_artifacts != artifacts:
                raise ValueError("authorization receipt closure changed before execution")
            validate_engine_requirement(plan.engine, plugins_enabled=plugins_enabled)
            current = evaluate_human_authorization(
                grant,
                trust_policy_path=default_human_authorization_trust_policy_path(),
                workspace=git_root,
                expected_request=expected_request,
                expected_review_items=review_items,
            )
            if current.status != "accepted":
                reasons = ", ".join(current.reason_codes) or "authorization_rejected"
                raise ValueError(f"authorization is not currently executable: {reasons}")
            payload = current.model_dump(mode="json")
            payload["command"] = expected_command
            if AuthorizationEvaluationV1.model_validate(payload) != verifier.authorization:
                raise ValueError("bound verifier authorization disagrees with current validation")

        revalidate_authority()
        if verifier.control.allowed_next_commands != [expected_command]:
            raise ValueError("bound verifier control does not expose exactly one guarded command")

        # ``plan``, ``report``, ``verifier``, and ``grant`` all came from the
        # same immutable, hash-validated byte snapshot loaded above.
        result = execute_pinned_git_push(
            grant.statement.request.operation,
            workspace=git_root,
            expected_source_tree_sha=git.head_tree_sha,
            revalidate_authority=revalidate_authority,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit {result.returncode}"
            raise _AuthorizedOperationFailed(
                f"authorized Git push failed without changing authority: {detail}"
            )
        typer.echo(
            json.dumps(
                {
                    "executed": True,
                    "authorization_id": grant.authorization_id,
                    "operation_id": grant.statement.request.operation.operation_id,
                },
                sort_keys=True,
            )
        )
    except _AuthorizedOperationFailed as exc:
        guidance = (
            "Inspect the protected executor failure, repair the host or destination, and "
            "rerun verification before retrying the guarded command."
        )
        _emit_authorization_error("other_error", exc, guidance)
        raise typer.Exit(4) from exc
    except (
        ConfigError,
        OSError,
        ValueError,
        ValidationError,
        json.JSONDecodeError,
    ) as exc:
        guidance = (
            "Rerun verification with a current signed grant; do not run the underlying "
            "Git operation directly."
        )
        _emit_authorization_error("input_parse_error", exc, guidance)
        raise typer.Exit(3) from exc


def _emit_authorization_error(error_kind: str, exc: Exception, guidance: str) -> None:
    action = NextAction(
        kind="review",
        why=guidance,
        expects="A fresh valid receipt and externally signed authorization closure.",
    ).model_dump(mode="json")
    typer.echo(
        json.dumps(
            {
                "error": error_kind,
                "message": str(exc),
                "next_action": guidance,
                "next_actions": [action],
            },
            sort_keys=True,
        ),
        err=True,
    )


def _load_json_bytes(data: bytes) -> dict[str, Any]:
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expected one JSON object")
    return payload


def _required_artifact(artifacts: dict[str, bytes], name: str) -> bytes:
    data = artifacts.get(name)
    if data is None:
        raise ValueError(f"receipt does not bind {name}")
    return data


__all__ = ["authorization_app"]
