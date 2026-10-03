"""Opt-in local execution; repository JSON can never substitute for a run."""
from __future__ import annotations

import hashlib
import json
import math
import os
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pydantic import ValidationError

from agents_shipgate.core.host_grants import (
    _openshell_public_value,
    build_host_boundary_snapshot,
)
from agents_shipgate.core.openshell import load_document, parse_policy
from agents_shipgate.core.static_inputs import read_static_input_bytes
from agents_shipgate.schemas.openshell_native import (
    DOMAINS,
    ExternalIdentity,
    NativeEnvelope,
    NativeObservation,
    NativeTrustConfig,
)

MAX_OUTPUT = 256 * 1024
MAX_EXECUTABLE = 64 * 1024 * 1024


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def external_path(value: str, workspace: Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or any(char in value for char in "\x00\r\n") or _openshell_public_value(value) != value:
        raise ValueError("trusted inputs require absolute external paths")
    lexical = Path(os.path.abspath(path))
    # Reject aliases rather than silently changing the operator's selection.
    if path != lexical or path.resolve() != lexical:
        raise ValueError("trusted inputs cannot traverse symbolic links")
    if lexical == workspace or workspace in lexical.parents:
        raise ValueError("native trust inputs must be outside the candidate workspace")
    return lexical


def _read_external_bytes(path: Path, limit: int) -> bytes:
    # Exact descriptor traversal avoids scanning an unrelated /tmp or /home
    # census. Every parent remains open and its named identity is rechecked.
    if os.name != "posix" or os.open not in os.supports_dir_fd:
        raise ValueError("trusted native input reads require POSIX descriptors")
    descriptors = []
    observed = []
    try:
        current = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(current)
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(child)
            observed.append((current, part, os.fstat(child)))
            current = child
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        descriptors.append(file_fd)
        before = os.fstat(file_fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit
                or before.st_uid not in {0, os.getuid()} or before.st_mode & 0o022):
            raise ValueError("trusted input is not one bounded regular file")
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(file_fd, min(1024 * 1024, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        if size > limit:
            raise ValueError("trusted input exceeds its read bound")
        def identity(metadata):
            return (metadata.st_dev, metadata.st_ino, metadata.st_mode)
        for parent, part, metadata in observed:
            if identity(os.stat(part, dir_fd=parent, follow_symlinks=False)) != identity(metadata):
                raise ValueError("trusted parent changed while reading")
        after = os.stat(path.name, dir_fd=current, follow_symlinks=False)
        if (identity(before), before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                identity(after), after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("trusted input changed while reading")
        return b"".join(chunks)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def capture_external(value: str, workspace: Path, identities: list, *, limit: int) -> bytes:
    path = external_path(value, workspace)
    try:
        data = _read_external_bytes(path, limit)
    except FileNotFoundError:
        identities.append(ExternalIdentity(path=str(path), state="absent"))
        raise
    except (OSError, ValueError):
        identities.append(ExternalIdentity(path=str(path), state="unconfirmable"))
        raise
    identities.append(ExternalIdentity(path=str(path), state="file", sha256=digest(data), size_bytes=len(data)))
    return data


def validate_external_currency(observation, workspace: Path) -> None:
    observed = NativeObservation.model_validate(observation)
    if observed.status == "within_boundary":
        if observed.provenance != "local_trusted_execution" or len(observed.external_inputs) != 3 or not observed.raw_result:
            raise ValueError("successful proof lacks local execution identity")
        if digest(observed.raw_result.encode()) != observed.raw_result_sha256:
            raise ValueError("native raw result identity mismatch")
        if validate_envelope(observed.raw_result.encode(), observed.actual_exit_code).result != "within_boundary":
            raise ValueError("native proof observation contradicts its raw result")
    for identity in observed.external_inputs:
        path = external_path(identity.path, workspace)
        if identity.state == "unconfirmable":
            raise ValueError("native trust input identity is unconfirmable; rerun verification")
        if identity.state == "absent":
            # lstat includes dangling links and directories in the negative lookup.
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raise ValueError("absent native trust input appeared; rerun verification")
        identities = []
        data = capture_external(str(path), workspace, identities, limit=MAX_EXECUTABLE)
        if digest(data) != identity.sha256 or len(data) != identity.size_bytes:
            raise ValueError("native trust input changed; rerun verification")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_config(data: bytes) -> NativeTrustConfig:
    value = _load_json(data)
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        raise ValueError("invalid native trust configuration")
    if _openshell_public_value(value) != value:
        raise ValueError("native configuration requires redaction")
    return NativeTrustConfig.model_validate(value)


def reject_native_output_overlap(config_path: Path | None, workspace: Path, out_dir: Path) -> None:
    """Inspect trusted selections before any output artifact can overwrite them."""
    if config_path is None:
        return
    try:
        config = load_config(capture_external(str(config_path), workspace, [], limit=64 * 1024))
    except (OSError, ValueError):
        return  # The evidence stage publishes its explicit invalid/absent route.
    output = out_dir.resolve()
    for selected in (config.executable.path, config.boundary.path):
        candidate = Path(selected).resolve()
        if candidate == output or output in candidate.parents:
            raise ValueError("Verifier output overlaps a selected native trust input")


def validate_envelope(raw: bytes, exit_code: int) -> NativeEnvelope:
    value = _load_json(raw)
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        raise ValueError("unknown native JSON schema")
    envelope = NativeEnvelope.model_validate(value)
    if _openshell_public_value(value) != value:
        raise ValueError("native output requires redaction")
    if envelope.inputs.model_dump() != {"candidate": "candidate.yaml", "boundary": "boundary.yaml"}:
        raise ValueError("native output names different inputs")
    expected = {"within_boundary": 0, "exceeds_boundary": 1, "unsupported": 3,
                "inconclusive": 130 if envelope.reason_code == "cancelled" else 3, "error": 2}
    if type(value.get("exit_code")) is not int or exit_code != envelope.exit_code or exit_code != expected[envelope.result]:
        raise ValueError("native exit/result mismatch")
    if envelope.result != "error" and (
        envelope.coverage is None or sorted(envelope.coverage.domains) != sorted(DOMAINS)
    ):
        raise ValueError("native output lacks required modeled domains")
    if envelope.result == "within_boundary" and any(
        item is not None for item in (envelope.counterexample, envelope.reason_code, envelope.reason)
    ):
        raise ValueError("contradictory successful proof")
    if envelope.result == "exceeds_boundary" and (
        not envelope.counterexample or envelope.counterexample.get("domain") not in {"filesystem", "network", "landlock", "process"}
        or envelope.reason_code is not None or envelope.reason is not None
    ):
        raise ValueError("invalid native counterexample")
    if envelope.result in {"unsupported", "inconclusive", "error"} and (
        envelope.reason_code not in {"unsupported_policy_shape", "unresolved_workdir", "unresolved_binary_path",
            "unresolved_filesystem_path", "solver_timeout", "solver_unknown", "resource_limit", "invalid_witness",
            "cancelled", "invalid_input"} or not envelope.reason or envelope.counterexample is not None
    ):
        raise ValueError("native failure lacks a reason")
    return envelope


def _limits(seconds: int) -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(seconds) + 2, math.ceil(seconds) + 2))
    # Darwin rejects RLIMIT_AS/RLIMIT_DATA; wall/CPU/output/file/descriptor
    # bounds still apply there. Linux additionally bounds virtual memory.
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def _execute(executable: bytes, candidate: bytes, boundary: bytes, seconds: int):
    """Copy captured bytes into a private directory and bound the whole group."""
    if os.name != "posix":
        return "unsupported", None, b""
    with tempfile.TemporaryDirectory(prefix="agents-shipgate-native-") as temp:
        root = Path(temp)
        for name, data in (("prover", executable), ("candidate.yaml", candidate), ("boundary.yaml", boundary)):
            path = root / name
            path.write_bytes(data)
            path.chmod(0o500 if name == "prover" else 0o400)
        command = [str(root / "prover"), "check", "candidate.yaml", "--boundary", "boundary.yaml", "--output", "json", "--timeout", f"{seconds}s"]
        process = subprocess.Popen(command, cwd=root, shell=False, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "HOME": temp, "LANG": "C.UTF-8"},
            preexec_fn=lambda: _limits(seconds))
        stdout = bytearray()
        sizes = {process.stdout: 0, process.stderr: 0}
        status = "finished"
        deadline = time.monotonic() + seconds + 2
        selector = selectors.DefaultSelector()
        for stream in sizes:
            selector.register(stream, selectors.EVENT_READ)
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "timeout"
                    break
                for key, _mask in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    sizes[key.fileobj] += len(chunk)
                    if sizes[key.fileobj] > MAX_OUTPUT:
                        status = "output_limit"
                        break
                    if key.fileobj is process.stdout:
                        stdout.extend(chunk)
                if status != "finished":
                    break
            if status == "finished":
                try:
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    status = "timeout"
        except KeyboardInterrupt:
            status = "cancelled"
        finally:
            # Also kills a child that closed its pipes but left descendants.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            selector.close()
            for stream in sizes:
                stream.close()
        return status, process.returncode, bytes(stdout)


def observe_native(*, config_path: Path | None, required: bool, workspace: Path, input_root: Path) -> NativeObservation:
    observation = NativeObservation(status="absent", reason_code="proof_not_supplied", required=required)
    if config_path is None:
        return observation
    identities = observation.external_inputs
    try:
        config_data = capture_external(str(config_path), workspace, identities, limit=64 * 1024)
        config = load_config(config_data)
        observation.required = required or config.required
        observation.candidate = {"selection": config.candidate.model_dump(mode="json")}
        executable = capture_external(config.executable.path, workspace, identities, limit=MAX_EXECUTABLE)
        boundary = capture_external(config.boundary.path, workspace, identities, limit=1024 * 1024)
        if digest(executable) != config.executable.sha256 or digest(boundary) != config.boundary.sha256:
            raise ValueError("pinned input identity mismatch")
        boundary_policy = parse_policy(boundary.decode("utf-8"))
        if _openshell_public_value(boundary_policy.model_dump(mode="json")) != boundary_policy.model_dump(mode="json"):
            raise ValueError("boundary requires redaction")
        snapshot = build_host_boundary_snapshot(input_root)
        registration = config.candidate.registration
        matching = [grant for grant in snapshot.inventory["grants"] if grant.get("kind") == "openshell_policy"
            and grant["facts"]["registration"] == registration and (
                (config.candidate.path and grant["source"] == config.candidate.path)
                or (config.candidate.composition and grant["facts"].get("composition", {}).get("name") == config.candidate.composition))]
        if len(matching) != 1 or any(issue["host"] == "openshell" and issue["blocking"] for issue in snapshot.inventory["issues"]):
            raise ValueError("candidate selection is incomplete or ambiguous")
        grant, = matching
        facts = grant["facts"]
        if config.candidate.path:
            artifact = next(item for item in snapshot.inventory["artifacts"] if item["host"] == "openshell"
                and item["kind"] == "openshell_policy" and item["path"] == config.candidate.path)
            target = (artifact.get("resolved_through") or [config.candidate.path])[-1]
            candidate = read_static_input_bytes(input_root / target, max_bytes=1024 * 1024)
            captured = snapshot.cache.openshell_input_reads[target]
            if captured.get("limit") or digest(candidate) != "sha256:" + captured["sha256"] or len(candidate) != captured["size_bytes"]:
                raise ValueError("candidate identity changed after extraction")
        else:
            candidate = _composed_candidate_bytes(facts, snapshot, input_root)
        snapshot.cache.finish()
        observation.candidate = {"selection": config.candidate.model_dump(mode="json"), "role": facts["role"],
            "sha256": digest(candidate), "size_bytes": len(candidate),
            "composition": facts.get("composition"), "runtime_freshness_verified": False}
        observation.invocation = ["openshell-prover", "check", "candidate.yaml", "--boundary", "boundary.yaml", "--output", "json", "--timeout", f"{config.timeout_seconds}s"]
        try:
            status, exit_code, raw = _execute(executable, candidate, boundary, config.timeout_seconds)
        except (OSError, subprocess.SubprocessError):
            observation.status, observation.reason_code = "error", "native_execution_error"
            return observation
        observation.actual_exit_code = exit_code
        if exit_code is not None:
            observation.provenance = "local_trusted_execution"
        if status != "finished":
            observation.status = status if status in {"timeout", "cancelled", "unsupported"} else "error"
            observation.reason_code = status
            return observation
        envelope = validate_envelope(raw, exit_code)
        observation.status = "cancelled" if envelope.reason_code == "cancelled" else envelope.result
        observation.reason_code = envelope.reason_code or envelope.result
        observation.raw_result = raw.decode("utf-8")
        observation.raw_result_sha256 = digest(raw)
        # A mutation during the call cannot establish current evidence.
        validate_external_currency(observation.model_dump(mode="json"), workspace)
        return observation
    except FileNotFoundError:
        observation.status, observation.reason_code = "absent", "trusted_input_unavailable"
    except (OSError, ValueError, ValidationError, UnicodeError, StopIteration):
        observation.status, observation.reason_code = "invalid", "native_input_or_result_invalid"
    return observation


def _composed_candidate_bytes(facts, snapshot, input_root: Path) -> bytes:
    """Use selected authored shapes, retaining omission rather than DTO defaults.

    The native model rejects some explicit extension fields even when their
    effective value is a runtime default. Contributor selection and generated
    keys come from the static composer; its normalized policy must agree with
    the reconstructed input before any execution.
    """
    def document(path, kind):
        artifact = next(item for item in snapshot.inventory["artifacts"]
                        if item["host"] == "openshell" and item["kind"] == kind and item["path"] == path)
        target = (artifact.get("resolved_through") or [path])[-1]
        raw = read_static_input_bytes(input_root / target, max_bytes=1024 * 1024)
        captured = snapshot.cache.openshell_input_reads[target]
        if captured.get("limit") or digest(raw) != "sha256:" + captured["sha256"] or len(raw) != captured["size_bytes"]:
            raise ValueError("composition contributor identity changed after extraction")
        return load_document(raw.decode("utf-8"))

    contributors = facts["composition"]["contributors"]
    selected, = [row for row in contributors if row["selected"] and row["role"] != "profile"]
    value = document(selected["path"], "openshell_policy")
    for row in contributors:
        if row["role"] != "profile" or not row["selected"]:
            continue
        profile = document(row["path"], "openshell_profile")
        value.setdefault("network_policies", {})[row["rule_key"]] = {
            "name": row["rule_key"], "endpoints": profile["endpoints"],
            "binaries": [{"path": path} for path in profile["binaries"]],
        }
    candidate = json.dumps(value, sort_keys=True).encode()
    if len(candidate) > 1024 * 1024 or parse_policy(candidate.decode()).model_dump(mode="json") != facts["policy"]:
        raise ValueError("reconstructed composition differs from static policy identity")
    return candidate


def _load_json(data):
    def reject_constant(_value):
        raise ValueError("native JSON contains a non-finite number")

    try:
        value = json.loads(data, object_pairs_hook=_unique_object, parse_constant=reject_constant)
    except RecursionError as exc:
        raise ValueError("native JSON exceeds parser depth") from exc
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 32 or nodes > 16384:
            raise ValueError("native JSON exceeds structural bounds")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def native_findings(observation: NativeObservation, context):
    from agents_shipgate.schemas.common import SourceReference
    from agents_shipgate.schemas.report import EvidenceGap, EvidenceGapAction, Finding

    if observation.status == "within_boundary":
        return []
    if observation.required and observation.status != "exceeds_boundary":
        context.policy_evidence_gaps.append(EvidenceGap(
            kind="invalid_evidence_provenance", subject="OpenShell required native containment",
            source_type="openshell_native", policy_id="SHIP-VERIFY-OPENSHELL-PROOF-UNAVAILABLE",
            why=f"Required modeled containment is unestablished: {observation.status} ({observation.reason_code}).",
            next_action=EvidenceGapAction(kind="provide_policy_evidence", why="Only a trusted local execution establishes native containment.",
                expects="An operator must repair the external trust configuration or unavailable execution, then rerun verify with the same proof options.")))
    if observation.status == "absent" and not observation.required:
        return []
    exceeds = observation.status == "exceeds_boundary"
    return [Finding(check_id="SHIP-VERIFY-OPENSHELL-BOUNDARY-EXCEEDED" if exceeds else "SHIP-VERIFY-OPENSHELL-PROOF-UNAVAILABLE",
        title="OpenShell candidate exceeds the trusted boundary" if exceeds else "OpenShell native containment is unestablished",
        severity="critical" if exceeds else "medium", category="verify", confidence="high",
        provenance_kind="runtime_trace", agent_id=context.agent.id,
        evidence={"status": observation.status, "reason_code": observation.reason_code, "required": observation.required},
        source=SourceReference(type="openshell_native", path=observation.candidate.get("selection", {}).get("registration")),
        blocks_release=exceeds,
        recommendation="Narrow the candidate and rerun trusted native proof; a successful proof does not approve other release obligations." if exceeds
            else "Repair the external trust inputs or unsupported proof context and rerun verify; do not supply a repository-authored result.")]
