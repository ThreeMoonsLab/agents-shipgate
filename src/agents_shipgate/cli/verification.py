"""Offline verification-plan, worker-IR, assembly, and reproduction commands."""

from __future__ import annotations

import contextlib
import json
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import typer

from agents_shipgate.cli._helpers import (
    _diagnose_config_error,
    _echo_next_action_hint,
)
from agents_shipgate.cli.agent_mode import (
    emit_agent_mode_error,
    emit_agent_mode_error_action,
)
from agents_shipgate.cli.diagnostics import top_next_actions
from agents_shipgate.cli.verify.git import (
    archive_tree,
    commit_date,
    commit_sha,
    diff_context,
    ensure_git_workspace,
    merge_base_sha,
    ref_exists,
    repository_identity,
    require_merge_base_sha,
    resolve_source_head_identity,
    tree_sha,
    validate_source_head_identity,
    working_tree_context,
    working_tree_paths,
)
from agents_shipgate.config.loader import load_yaml_file
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    read_static_input_text,
    reset_static_input_snapshot,
)
from agents_shipgate.core.verification_identity import (
    build_executor,
    build_terminal_receipt,
    build_unit_result,
    build_verification_plan,
    sha256_file,
    validate_engine_requirement,
    validate_plan_inputs,
    validate_receipt_artifacts,
)
from agents_shipgate.packet.json_packet import load_packet_json, write_packet_json
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.schemas.diagnostics import NextAction
from agents_shipgate.schemas.report import ReadinessReport
from agents_shipgate.schemas.verification_identity import (
    VerificationPlan,
    VerificationReceipt,
    VerificationUnitResult,
    content_id,
)
from agents_shipgate.schemas.verifier import VerifierArtifact
from agents_shipgate.schemas.verify_run import (
    VerifyRunArtifactRef,
    VerifyRunOutcome,
    build_verify_run_artifact,
)

verification_app = typer.Typer(no_args_is_help=True)

#: Matches the verifier's worktree changed-file ceiling.
_MAX_CHANGED_FILE_BYTES = 64 * 1024 * 1024


@verification_app.command("prepare")
def prepare(
    workspace: Path = typer.Option(Path("."), "--workspace"),
    config: Path = typer.Option(Path("shipgate.yaml"), "--config", "-c"),
    base: str | None = typer.Option(None, "--base"),
    head: str | None = typer.Option(None, "--head"),
    baseline: Path | None = typer.Option(None, "--baseline"),
    diff_from: Path | None = typer.Option(None, "--diff-from"),
    policy_packs: list[Path] | None = typer.Option(None, "--policy-pack"),
    ci_mode: str = typer.Option("advisory", "--ci-mode"),
    no_plugins: bool = typer.Option(False, "--no-plugins"),
    no_heuristics: bool = typer.Option(False, "--no-heuristics"),
    evaluation_date: str | None = typer.Option(None, "--evaluation-date"),
    out: Path = typer.Option(Path("agents-shipgate-reports/verification-plan.json"), "--out"),
) -> None:
    """Prepare one portable, content-addressed plan without evaluating policy."""

    root = ensure_git_workspace(workspace.resolve())
    if head is not None and base is None:
        raise typer.BadParameter("--head requires --base so the prepared diff has an exact range")
    head_ref = head or "HEAD"
    if not ref_exists(root, head_ref):
        raise typer.BadParameter(f"head ref is unavailable locally: {head_ref}")
    worktree_overlay_paths: list[str] | None = None
    if head is not None:
        changed, diff_text = diff_context(root, base, head_ref)
    elif base:
        worktree_overlay_paths = working_tree_paths(root)
        effective_base = require_merge_base_sha(root, base, head_ref)
        changed, diff_text = working_tree_context(
            root,
            comparison_ref=effective_base,
            reject_index_hidden=True,
        )
    else:
        worktree_overlay_paths, overlay_diff = working_tree_context(
            root,
            reject_index_hidden=True,
        )
        changed, diff_text = worktree_overlay_paths, overlay_diff
    resolved_date = evaluation_date or commit_date(root, head_ref)
    config_relative = _under(root, config).relative_to(root)
    policy_paths = [_under(root, path) for path in policy_packs or []]
    baseline_path = _under(root, baseline) if baseline is not None else None
    diff_from_path = _under(root, diff_from) if diff_from is not None else None
    git_identity = _git_identity(root, base, head_ref)

    # A manifest whose inputs cannot be read, or that declares one the plan
    # cannot bind, is the caller's to fix — route it like every other input
    # failure instead of surfacing a traceback out of the identity builder.
    try:
        plan = _build_plan(
            root=root,
            head=head,
            head_ref=head_ref,
            base=base,
            config_relative=config_relative,
            baseline_path=baseline_path,
            diff_from_path=diff_from_path,
            policy_paths=policy_paths,
            changed=changed,
            diff_text=diff_text,
            resolved_date=resolved_date,
            git_identity=git_identity,
            ci_mode=ci_mode,
            no_plugins=no_plugins,
            no_heuristics=no_heuristics,
            worktree_overlay_paths=worktree_overlay_paths,
        )
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        emit_agent_mode_error_action(
            "input_parse_error",
            message=exc,
            exit_code=3,
            action=NextAction(
                kind="review",
                why=(
                    "Inspect the file referenced in the error; ensure it exists, "
                    "is valid, and resolves under the manifest directory."
                ),
                expects=(
                    "Referenced file is present, parseable, and inside the manifest directory."
                ),
            ),
        )
        raise typer.Exit(3) from exc
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        # Route through the shared diagnostic catalog rather than a local
        # guess, so a missing manifest gets the setup route, an unparseable one
        # gets the edit route, and an unresolved adapter gets its own — exactly
        # as `scan` and `doctor` already answer.
        #
        # Diagnose the exact manifest this invocation asked for. Passing a
        # workspace instead makes the catalog discover every `shipgate.yaml`
        # beneath it and describe the first one that happens to parse, so a
        # monorepo — or any `--config` naming a file that is not there — would
        # be told to edit an unrelated, perfectly valid manifest. `prepare`
        # always resolves one exact path, so there is nothing to discover.
        actions = top_next_actions(
            _diagnose_config_error(
                config=str(root / config_relative),
                workspace=None,
                exc=exc,
                plugins_enabled=False if no_plugins else None,
            )
        )
        _echo_next_action_hint(actions)
        emit_agent_mode_error(
            "config_error",
            message=str(exc),
            exit_code=2,
            next_action=actions[0].to_legacy_string(),
            next_actions=[action.model_dump(mode="json") for action in actions],
        )
        raise typer.Exit(2) from exc
    diff_path = out.with_name("verification-input.diff")
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(diff_text, encoding="utf-8")
    _write_model(out, plan)
    typer.echo(
        json.dumps(
            {
                "request_id": plan.request_id,
                "plan": str(out),
                "diff": str(diff_path),
            }
        )
    )


def _build_plan(
    *,
    root: Path,
    head: str | None,
    head_ref: str,
    base: str | None,
    config_relative: Path,
    baseline_path: Path | None,
    diff_from_path: Path | None,
    policy_paths: list[Path],
    changed: list[str],
    diff_text: str,
    resolved_date: str,
    git_identity: dict[str, str | None],
    ci_mode: str,
    no_plugins: bool,
    no_heuristics: bool,
    worktree_overlay_paths: list[str] | None = None,
) -> VerificationPlan:
    """Build the worktree or committed-tree plan for ``prepare``."""

    options = {
        "ci_mode": ci_mode,
        "no_heuristics": no_heuristics,
        "plugins_enabled": not no_plugins,
    }
    if head is None:
        with _captured_inputs(
            config_path=root / config_relative,
            input_root=root,
            policy_pack_paths=policy_paths,
            plugins_enabled=False if no_plugins else None,
            # The overlay set is HEAD-relative and ``changed`` is merge-base-
            # relative, so a canceled path appears only in the former. Bind the
            # union: an overlay path the snapshot contains but never read is
            # reported as absent, which would attest a present file as deleted.
            changed_files=sorted({*changed, *(worktree_overlay_paths or [])}),
            plan_inputs=[
                path
                for path in (baseline_path, diff_from_path, *policy_paths)
                if path is not None
            ],
        ) as captured:
            return build_verification_plan(
                git_root=root,
                input_root=root,
                config_path=root / config_relative,
                config_logical_path=config_relative.as_posix(),
                base_ref=base,
                head_ref=head_ref,
                archived_head=False,
                source_head_commit_sha=None,
                **git_identity,
                changed_files=changed,
                diff_text=diff_text,
                baseline_path=baseline_path,
                diff_from_path=diff_from_path,
                policy_pack_paths=policy_paths,
                evaluation_date=resolved_date,
                options=options,
                plugins_enabled=False if no_plugins else None,
                worktree_overlay_paths=worktree_overlay_paths,
                captured_input_paths=captured,
            )
    source_identity = resolve_source_head_identity(root, head_ref=head_ref)
    with tempfile.TemporaryDirectory(prefix="agents-shipgate-plan-") as tmp:
        snapshot = Path(tmp) / "snapshot"
        archive_tree(root, head_ref, snapshot)
        # Resolve once the tree exists. The snapshot matches paths lexically, and
        # on macOS the temporary directory is reached through /var while every
        # path derived below resolves to /private/var — leaving them in two
        # spellings makes `contains()` false for inputs that are plainly inside.
        snapshot = snapshot.resolve()
        mapped_policy_paths = [_map(snapshot, root, path) for path in policy_paths]
        mapped_baseline_path = _map(snapshot, root, baseline_path)
        with _captured_inputs(
            config_path=snapshot / config_relative,
            input_root=snapshot,
            policy_pack_paths=mapped_policy_paths,
            plugins_enabled=False if no_plugins else None,
            changed_files=changed,
            # The comparison report is never mapped into the tree, so it stays
            # outside the evaluated root and must be bound as an external input.
            plan_inputs=[
                path
                for path in (mapped_baseline_path, diff_from_path, *mapped_policy_paths)
                if path is not None
            ],
        ) as captured:
            return build_verification_plan(
                git_root=root,
                input_root=snapshot,
                config_path=snapshot / config_relative,
                config_logical_path=config_relative.as_posix(),
                base_ref=base,
                head_ref=head_ref,
                archived_head=True,
                source_head_commit_sha=source_identity.source_head_commit_sha,
                **git_identity,
                changed_files=changed,
                diff_text=diff_text,
                baseline_path=mapped_baseline_path,
                diff_from_path=diff_from_path,
                policy_pack_paths=mapped_policy_paths,
                evaluation_date=resolved_date,
                options=options,
                captured_input_paths=captured,
                plugins_enabled=False if no_plugins else None,
            )


@contextlib.contextmanager
def _captured_inputs(
    *,
    config_path: Path,
    input_root: Path,
    policy_pack_paths: list[Path],
    plugins_enabled: bool | None,
    changed_files: list[str],
    plan_inputs: list[Path],
) -> Iterator[list[Path]]:
    """Yield the paths the adapters opened, with their bytes still bound.

    Enumerating the manifest reaches only what it names. An input discovered
    while parsing something the manifest names — a Google ADK ``McpToolset``
    inventory, an OpenAPI spec referenced from Python, a sub-agent config —
    is invisible to that walk, so a plan built from declarations alone can be
    byte-identical across two trees whose adapters read different bytes.
    Only the read boundary sees those, and it is the same boundary ``verify``
    binds its receipt to.

    The snapshot stays active for the caller's plan construction. Handing back
    a path list and letting the builder reopen the files would take the paths
    and their hashes from two different instants: a file rewritten in between
    would be attested at its new content while the path list still describes
    what the old content pointed at.

    That makes binding an obligation rather than an optimization. Under an
    active snapshot both ``_blobs`` and ``_optional_blob`` read a path that is
    *contained but never read* as absent, so every input the plan will hash has
    to be bound here — ``plan_inputs`` (the baseline, comparison report, and
    policy packs) and the changed files as well as whatever the adapters open.
    Miss one and it does not merely go unbound: it disappears from the plan.

    ``prepare`` still evaluates no policy: source loading is static, local,
    network-free, and decision-free, and the scan phases after it make no
    further declared-input reads. It does mean ``prepare`` now fails on a
    manifest whose inputs cannot be loaded — which is exactly when it cannot
    honestly claim an input set, and when ``verify`` would fail too.
    """

    from agents_shipgate.cli.scan.inputs import _load_inputs
    from agents_shipgate.cli.scan.prepare import _prepare_scan

    if not config_path.exists():
        # A manifest that is not there is a configuration problem with its own
        # published route, not an input that moved mid-run. Resolve it before
        # the capture below, which would otherwise recast the
        # FileNotFoundError as "inputs changed while they were being read" and
        # send an agent to the wrong recovery. Delegating to the loader keeps
        # the message and its `init` hint in one place.
        load_yaml_file(config_path)
    resolved_root = input_root.resolve()
    # A plan input outside the evaluated root — the comparison report on a
    # committed-tree preparation — has to be declared external or the snapshot
    # refuses to read it at all.
    snapshot = StaticInputSnapshot(
        resolved_root,
        external_paths=[
            path
            for path in plan_inputs
            if resolved_root not in path.resolve().parents
        ],
    )
    token = activate_static_input_snapshot(snapshot)
    try:
        try:
            # Read the manifest once, through the snapshot, and parse from those
            # exact bytes. `load_manifest_with_positions` otherwise reads it
            # twice — a direct `Path.read_text` for the model, then the snapshot
            # for positions — so a rewrite between the two would let the
            # adapters follow one manifest while the plan hashes the other.
            resolved = _prepare_scan(
                config_path=config_path,
                ci_mode=None,
                fail_on=None,
                output_dir=None,
                formats=None,
                packet_enabled=None,
                packet_formats=None,
                baseline_mode="new-findings",
                manifest_text=read_static_input_text(
                    config_path,
                    max_bytes=_MAX_CHANGED_FILE_BYTES,
                ),
            )
            _load_inputs(
                manifest=resolved.manifest,
                base_dir=resolved.base_dir,
                config_path=config_path,
                policy_pack_paths=policy_pack_paths,
                verbose=False,
                plugins_enabled=plugins_enabled,
            )
            for candidate in (
                *plan_inputs,
                *(resolved_root / relative for relative in changed_files),
            ):
                if (
                    candidate.is_file()
                    and not candidate.is_symlink()
                    and snapshot.contains(candidate)
                    and not snapshot.has(candidate)
                ):
                    snapshot.read_bytes(candidate, max_bytes=_MAX_CHANGED_FILE_BYTES)
            snapshot.finish()
        except (OSError, ValueError) as exc:
            raise InputParseError(
                f"Verification inputs changed while they were being read: {exc}"
            ) from exc
        # Blob paths are logical and relative to the input root, so a read
        # outside it cannot become one.
        yield [path for path in snapshot.paths() if resolved_root in path.parents]
    finally:
        reset_static_input_snapshot(token)


@verification_app.command("worker")
def worker(
    plan_path: Path = typer.Option(..., "--plan"),
    workspace: Path = typer.Option(Path("."), "--workspace"),
    diff_path: Path | None = typer.Option(None, "--diff"),
    out: Path = typer.Option(
        Path("agents-shipgate-reports/verification-unit-result.json"), "--out"
    ),
) -> None:
    """Validate one task's immutable inputs and emit decision-free worker IR."""

    plan = VerificationPlan.model_validate(_load_json(plan_path))
    resolved_diff = diff_path or plan_path.with_name(plan.inputs.diff.path)
    try:
        validate_engine_requirement(
            plan.engine,
            plugins_enabled=_plugins_enabled(plan),
        )
        validate_plan_inputs(
            plan,
            root=workspace,
            diff_path=resolved_diff,
            bundle_root=plan_path.parent,
        )
        _validate_git_subject(plan, workspace)
    except ValueError as exc:
        raise InputParseError(str(exc)) from exc
    unit = build_unit_result(
        plan=plan,
        normalized_ir={
            "input_set_id": plan.inputs.input_set_id,
            "subject_id": plan.subject.subject_id,
            "validated_paths": sorted(
                {
                    plan.inputs.config.path,
                    plan.inputs.diff.path,
                    *(blob.path for blob in plan.inputs.tool_sources),
                    *(blob.path for blob in plan.inputs.policy_packs),
                }
            ),
        },
    )
    _write_model(out, unit)
    typer.echo(json.dumps({"unit_result_id": unit.unit_result_id, "result": str(out)}))


@verification_app.command("assemble")
def assemble(
    plan_path: Path = typer.Option(..., "--plan"),
    unit_paths: list[Path] = typer.Option(..., "--unit-result"),
    verifier_path: Path = typer.Option(..., "--verifier"),
    artifacts_root: Path = typer.Option(Path("."), "--artifacts-root"),
    out: Path = typer.Option(Path("agents-shipgate-reports/verification-receipt.json"), "--out"),
) -> None:
    """Validate worker IR and close the sole-engine verifier projections."""

    plan = VerificationPlan.model_validate(_load_json(plan_path))
    units = [VerificationUnitResult.model_validate(_load_json(path)) for path in unit_paths]
    verifier = VerifierArtifact.model_validate(_load_json(verifier_path))
    try:
        validate_engine_requirement(
            plan.engine,
            plugins_enabled=_plugins_enabled(plan),
        )
    except ValueError as exc:
        raise InputParseError(str(exc)) from exc
    verifier_mirrors = {
        "request_id": plan.request_id,
        "subject_id": plan.subject.subject_id,
        "input_set_id": plan.inputs.input_set_id,
        "engine_requirement_id": plan.engine.engine_requirement_id,
    }
    for field, expected in verifier_mirrors.items():
        if getattr(verifier, field) != expected:
            raise InputParseError(f"verifier {field} does not match the plan")
    if not units:
        raise InputParseError("assembly requires at least one worker result")
    if len({unit.unit_result_id for unit in units}) != len(units):
        raise InputParseError("assembly rejects duplicate worker results")
    executor_ids = {unit.executor.executor_id for unit in units}
    if len(executor_ids) != 1:
        raise InputParseError("assembly requires one exact executor identity")
    if any(unit.request_id != plan.request_id for unit in units):
        raise InputParseError("worker result request_id does not match the plan")
    if any(unit.status != "succeeded" for unit in units):
        raise InputParseError("failed worker results cannot produce a terminal receipt")
    expected_decision_id = content_id(
        {
            "request_id": plan.request_id,
            "unit_result_ids": sorted(unit.unit_result_id for unit in units),
            "decision": verifier.decision,
            "merge_verdict": verifier.merge_verdict,
            "can_merge_without_human": verifier.can_merge_without_human,
        }
    )
    resolved_artifact_root = artifacts_root.resolve()
    regenerated_artifacts = {
        "agent_handoff_json",
        "verification_artifact_manifest_json",
        "verification_input_diff",
        "verification_plan_json",
        "verification_receipt_json",
        "verification_unit_result_json",
        "verify_run_json",
        "verifier_json",
    }
    artifact_paths = {
        name: _resolve_artifact_ref(resolved_artifact_root, value)
        for name, value in verifier.artifacts.items()
        if name not in regenerated_artifacts
    }
    artifact_paths.update(
        {
            "verification_plan_json": plan_path.resolve(),
            "verification_input_diff": plan_path.with_name(plan.inputs.diff.path).resolve(),
            **{
                f"verification_unit_result_{index}": path.resolve()
                for index, path in enumerate(unit_paths)
            },
            "verifier_json": verifier_path.resolve(),
        }
    )
    diff_artifact = artifact_paths["verification_input_diff"]
    if not diff_artifact.is_file():
        raise InputParseError(f"prepared diff artifact is missing: {plan.inputs.diff.path}")
    executor = build_executor(plan.engine)
    if executor.executor_id not in executor_ids:
        raise InputParseError("worker executor does not satisfy the prepared engine")

    verifier_payload = verifier.model_dump(mode="json")
    verifier_payload.update(
        {
            "request_id": plan.request_id,
            "subject_id": plan.subject.subject_id,
            "input_set_id": plan.inputs.input_set_id,
            "engine_requirement_id": plan.engine.engine_requirement_id,
            "executor_id": executor.executor_id,
            "decision_id": expected_decision_id,
        }
    )
    verifier = VerifierArtifact.model_validate(verifier_payload)

    report_path = artifact_paths.get("report_json")
    report: ReadinessReport | None = None
    if report_path is not None:
        report = ReadinessReport.model_validate(_load_json(report_path))
        report.request_id = plan.request_id
        report.subject_id = plan.subject.subject_id
        report.input_set_id = plan.inputs.input_set_id
        report.engine_requirement_id = plan.engine.engine_requirement_id
        report.decision_id = expected_decision_id
        report_path.write_text(
            json.dumps(report_json_payload(report), indent=2),
            encoding="utf-8",
        )

    packet_path = artifact_paths.get("packet_json")
    if packet_path is not None:
        packet = load_packet_json(packet_path.read_text(encoding="utf-8"))
        packet.request_id = plan.request_id
        packet.subject_id = plan.subject.subject_id
        packet.input_set_id = plan.inputs.input_set_id
        packet.engine_requirement_id = plan.engine.engine_requirement_id
        packet.decision_id = expected_decision_id
        write_packet_json(packet, packet_path)

    _write_model(verifier_path, verifier)
    verify_run_path = artifact_paths.get(
        "verify_run_json", resolved_artifact_root / "verify-run.json"
    )
    verify_run_refs = {
        name: VerifyRunArtifactRef(
            path=path.relative_to(resolved_artifact_root).as_posix(),
            sha256=_sha256_hex(path),
        )
        for name, path in sorted(artifact_paths.items())
        if name
        not in {
            "verify_run_json",
            "agent_handoff_json",
            "verification_plan_json",
            "verification_input_diff",
        }
        and path.is_file()
        and _is_under(path, resolved_artifact_root)
    }
    outcome = VerifyRunOutcome(
        exit_code=verifier.head_exit_code,
        base_status=verifier.base_status,
        execution=verifier.execution,
        applicability=verifier.applicability,
        decision=verifier.decision,
        merge_verdict=verifier.merge_verdict,
        can_merge_without_human=verifier.can_merge_without_human,
        control=verifier.control,
    )
    verify_run = build_verify_run_artifact(
        plan=plan,
        executor=executor,
        unit_result_ids=[unit.unit_result_id for unit in units],
        outcome=outcome,
        artifacts=verify_run_refs,
    )
    _write_model(verify_run_path, verify_run)
    artifact_paths["verify_run_json"] = verify_run_path

    handoff_path = artifact_paths.get(
        "agent_handoff_json", resolved_artifact_root / "agent-handoff.json"
    )
    handoff = build_agent_handoff(
        verifier=verifier,
        report=report,
        verify_run=verify_run,
    )
    _write_model(handoff_path, handoff)
    artifact_paths["agent_handoff_json"] = handoff_path

    manifest, receipt = build_terminal_receipt(
        plan=plan,
        unit_results=units,
        decision=verifier.decision,
        merge_verdict=verifier.merge_verdict,
        can_merge_without_human=verifier.can_merge_without_human,
        artifact_paths=artifact_paths,
        artifact_root=resolved_artifact_root,
    )
    if not _is_under(out, resolved_artifact_root):
        raise InputParseError("receipt output must remain under --artifacts-root")
    _write_model(resolved_artifact_root / "verification-artifacts.json", manifest)
    _write_model(out, receipt)
    typer.echo(json.dumps({"receipt_id": receipt.receipt_id, "receipt": str(out)}))


@verification_app.command("reproduce")
def reproduce(
    receipt_path: Path = typer.Option(..., "--receipt"),
    artifacts_root: Path = typer.Option(Path("."), "--artifacts-root"),
) -> None:
    """Validate a terminal receipt and every artifact hash without execution."""

    receipt = VerificationReceipt.model_validate(_load_json(receipt_path))
    try:
        validate_receipt_artifacts(receipt, root=artifacts_root.resolve())
    except ValueError as exc:
        raise InputParseError(str(exc)) from exc
    typer.echo(json.dumps({"valid": True, "receipt_id": receipt.receipt_id}))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputParseError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InputParseError(f"Expected one JSON object in {path}")
    return value


def _write_model(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _under(root: Path, path: Path) -> Path:
    candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ConfigError(f"Path escapes workspace: {path}")
    return candidate


def _map(snapshot: Path, root: Path, path: Path | None) -> Path | None:
    if path is None:
        return None
    return snapshot / path.resolve().relative_to(root.resolve())


def _resolve_artifact_ref(root: Path, value: str) -> Path:
    logical = Path(value)
    candidates: list[Path] = []
    if not logical.is_absolute():
        candidates.append((root / logical).resolve())
    candidates.append((root / logical.name).resolve())
    valid = sorted(
        {
            candidate
            for candidate in candidates
            if candidate.is_file() and (candidate == root or root in candidate.parents)
        }
    )
    if len(valid) != 1:
        raise InputParseError(f"artifact reference {value!r} did not resolve uniquely under {root}")
    return valid[0]


def _sha256_hex(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def _is_under(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return resolved == root or root in resolved.parents


def _plugins_enabled(plan: VerificationPlan) -> bool | None:
    value = plan.inputs.options.get("plugins_enabled")
    return value if isinstance(value, bool) else None


def _validate_git_subject(plan: VerificationPlan, workspace: Path) -> None:
    root = ensure_git_workspace(workspace.resolve())
    subject = plan.subject.git
    if repository_identity(root) != subject.repository_id:
        raise ValueError("worker repository identity does not match the plan")

    if subject.head_commit_sha is not None:
        if commit_sha(root, subject.head_commit_sha) != subject.head_commit_sha:
            raise ValueError("worker lacks the plan's exact head commit")
        if tree_sha(root, subject.head_commit_sha) != subject.head_tree_sha:
            raise ValueError("worker head tree does not match the plan")
    if subject.base_commit_sha is not None:
        if commit_sha(root, subject.base_commit_sha) != subject.base_commit_sha:
            raise ValueError("worker lacks the plan's exact base commit")
        if tree_sha(root, subject.base_commit_sha) != subject.base_tree_sha:
            raise ValueError("worker base tree does not match the plan")
    if subject.merge_base_sha is not None:
        if subject.base_commit_sha is None or subject.head_commit_sha is None:
            raise ValueError("plan merge base lacks exact base/head commits")
        actual_merge_base = merge_base_sha(
            root,
            subject.base_commit_sha,
            subject.head_commit_sha,
        )
        if actual_merge_base != subject.merge_base_sha:
            raise ValueError("worker merge base does not match the plan")
    if subject.snapshot_kind == "committed_tree":
        if subject.head_commit_sha is None:
            raise ValueError("committed plan lacks its exact evaluated head commit")
        validate_source_head_identity(
            root,
            evaluated_head_commit_sha=subject.head_commit_sha,
            source_head_commit_sha=subject.source_head_commit_sha,
        )
    if subject.snapshot_kind == "worktree_overlay":
        if subject.source_head_commit_sha is not None:
            raise ValueError("worktree-overlay plan cannot carry source-head authority")
        if commit_sha(root, "HEAD") != subject.head_commit_sha:
            raise ValueError("worker HEAD does not match the worktree-overlay plan")
        declared_overlay_paths = plan.inputs.options.get("worktree_overlay_paths")
        if declared_overlay_paths is None:
            raise ValueError(
                "worktree-overlay plan predates overlay mode binding; "
                "re-run `agents-shipgate verification prepare`"
            )
        elif not isinstance(declared_overlay_paths, list) or not all(
            isinstance(path, str) for path in declared_overlay_paths
        ):
            raise ValueError("plan worktree_overlay_paths must be a string list")
        elif declared_overlay_paths != sorted(set(declared_overlay_paths)):
            raise ValueError("plan worktree_overlay_paths must be sorted and unique")
        else:
            overlay_paths = declared_overlay_paths
        rows: list[dict[str, Any]] = []
        for relative in overlay_paths:
            candidate = (root / relative).resolve()
            if candidate != root and root not in candidate.parents:
                raise ValueError(f"plan changed path escapes workspace: {relative}")
            present = candidate.is_file()
            rows.append(
                {
                    "path": relative,
                    "status": "present" if present else "deleted",
                    "sha256": sha256_file(candidate) if present else None,
                    "git_mode": (
                        "100755"
                        if present
                        and stat.S_IMODE(candidate.stat().st_mode) & stat.S_IXUSR
                        else "100644"
                        if present
                        else None
                    ),
                }
            )
        actual_overlay = content_id(rows) if rows else None
        if actual_overlay != subject.worktree_overlay_sha256:
            raise ValueError("worker worktree overlay does not match the plan")


def _git_identity(root: Path, base: str | None, head: str) -> dict[str, str | None]:
    base_commit = commit_sha(root, base) if base else None
    head_commit = commit_sha(root, head)
    return {
        "repository_id": repository_identity(root),
        "base_commit_sha": base_commit,
        "base_tree_sha": tree_sha(root, base) if base and base_commit else None,
        "head_commit_sha": head_commit,
        "head_tree_sha": tree_sha(root, head) if head_commit else None,
        "merge_base_sha": merge_base_sha(root, base, head) if base and base_commit else None,
    }


__all__ = ["verification_app"]
