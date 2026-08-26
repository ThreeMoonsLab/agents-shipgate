from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_shipgate.config.loader import load_manifest_text, manifest_read_error
from agents_shipgate.core.adoption_ladder import adoption_rung
from agents_shipgate.core.artifact_models import (
    AnthropicArtifacts,
    CodexPluginArtifacts,
    ConductorArtifacts,
    CrewAiArtifacts,
    GoogleAdkArtifacts,
    LangChainArtifacts,
    N8nArtifacts,
    OpenAIApiArtifacts,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.manifest_protection import manifest_protection
from agents_shipgate.core.privacy import RedactionStats, redact_data
from agents_shipgate.inputs.policy_packs import load_policy_packs
from agents_shipgate.inputs.protocol import REGISTRY

from .path_helpers import _default_baseline_status
from .source_loading import (
    _artifact_warnings,
    _build_canonical_tools,
    _load_sources,
)
from .surface_redaction import _frameworks_surface
from .validation import _resolve_source_paths

# Out-of-band key carrying the manifest bytes this inspection read. Popped by
# the caller before the payload is published; it is not part of the doctor JSON.
MANIFEST_SNAPSHOT_KEY = "__manifest_bytes__"


def decode_manifest(data: bytes, source: Path | str) -> str:
    """Decode manifest bytes strictly, or refuse them as a config error.

    ``errors="replace"`` does not read a manifest, it *writes a different one*:
    a single ``0xff`` in ``project.name`` became U+FFFD, so `doctor` loaded a
    valid manifest, reported `setup_complete`, and recommended verify — while
    `scan` on the same file exited 4 with `UnicodeDecodeError`. Setup and the
    gate have to validate the same input language, and the one that says "this
    is fine" must not be the lenient one.
    """

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"{source} is not valid UTF-8 and cannot be read as a manifest: {exc}"
        ) from exc


def inspect_sources(
    *,
    config_path: Path,
    verbose: bool = False,
    plugins_enabled: bool | None = None,
    manifest_bytes: bytes | None = None,
) -> dict[str, object]:
    """``doctor``'s manifest-introspection entry point.

    v0.20 (PR #111 review fix follow-up #3): mirrors ``_load_inputs``'s
    per-scan registry clone + adapter discovery so ``doctor`` can
    inspect manifests that reference third-party source types. Before
    this fix, the global ``REGISTRY`` was builtin-only (by design,
    after the per-scan-registry refactor), so a manifest with
    ``tool_sources[].type: demo_source`` scanned fine but ``doctor``
    raised ``ConfigError: No adapter registered``.
    """

    from agents_shipgate.inputs.protocol import discover_third_party_adapters

    # One read, and the parse comes from it. Loading the path and then reopening
    # it for the bytes was two generations: an edit landing between them let
    # doctor route from manifest A while the identity of that answer hashed
    # manifest B, so the payload described no single filesystem state. The
    # caller may hand its own snapshot in, so that a *failure* here is reported
    # against the same bytes too.
    if manifest_bytes is None:
        try:
            manifest_bytes = config_path.read_bytes()
        except OSError as exc:
            # Absent is not empty. Letting the read failure fall through as
            # ``b""`` made a manifest that was never created report as one
            # with the wrong shape (#384); the errno is only in hand here.
            raise manifest_read_error(config_path, exc) from exc
    manifest = load_manifest_text(decode_manifest(manifest_bytes, config_path), source=config_path)
    # The manifest as written, kept before the filtering below removes sources
    # that could not be loaded. Adoption is a statement about what a human
    # declared, so it has to read the declared file: a source whose path is
    # broken still carries its reviewed ``authority`` answer, and reading the
    # filtered copy reported such a repository as rung 1 while its manifest
    # plainly held a rung-2 answer.
    declared_manifest = manifest
    base_dir = config_path.resolve().parent
    unresolved_sources = _resolve_source_paths(manifest, base_dir, config_path)
    if unresolved_sources:
        # Drop unresolved-required sources from the manifest before loading
        # so doctor returns a structured payload with `unresolved_sources`
        # instead of raising InputParseError. scan() does not use this path
        # — its `_load_sources` call is unchanged and still raises.
        unresolved_ids = {entry["id"] for entry in unresolved_sources}
        manifest = manifest.model_copy(
            update={
                "tool_sources": [
                    src for src in manifest.tool_sources
                    if src.id not in unresolved_ids
                ]
            }
        )
    # v0.20 (PR #111 review follow-up #3): build a per-scan registry
    # for ``doctor`` so it sees the same adapter set as ``scan``. The
    # global ``REGISTRY`` is builtin-only by design after the
    # per-scan-clone refactor; without this discovery step,
    # third-party source types would be unresolvable here.
    scan_registry = REGISTRY.clone()
    loaded_adapters: list[dict[str, Any]] = []
    discovery_records = discover_third_party_adapters(
        scan_registry,
        plugins_enabled=plugins_enabled,
        loaded_adapters=loaded_adapters,
    )
    third_party_records: dict[str, Any] = {
        record.adapter.source_type: record
        for record in discovery_records
        if record.adapter is not None
    }
    loaded_sources, artifact_bag = _load_sources(
        manifest,
        base_dir,
        verbose=verbose,
        registry=scan_registry,
        third_party_records=third_party_records,
        plugins_enabled=plugins_enabled,
    )
    adk_artifacts = artifact_bag.get("google_adk", GoogleAdkArtifacts)
    langchain_artifacts = artifact_bag.get("langchain", LangChainArtifacts)
    crewai_artifacts = artifact_bag.get("crewai", CrewAiArtifacts)
    n8n_artifacts = artifact_bag.get("n8n", N8nArtifacts)
    conductor_artifacts = artifact_bag.get("conductor", ConductorArtifacts)
    api_artifacts = artifact_bag.get("openai_api", OpenAIApiArtifacts)
    anthropic_artifacts = artifact_bag.get("anthropic_api", AnthropicArtifacts)
    codex_plugin_artifacts = artifact_bag.get("codex_plugin", CodexPluginArtifacts)
    tools, identity_warnings = _build_canonical_tools(loaded_sources)
    warnings = [warning for loaded in loaded_sources for warning in loaded.warnings]
    warnings.extend(identity_warnings)
    warnings.extend(_artifact_warnings(artifact_bag))
    policy_packs = load_policy_packs(manifest, base_dir)
    warnings.extend(policy_packs.warnings)
    # Some adapters expose the same warnings through both LoadedToolSource
    # and the artifact bag; keep doctor warning output stable and unique.
    warnings = list(dict.fromkeys(warnings))
    rung = adoption_rung(declared_manifest, manifest_protection(config_path))
    payload = {
        "project": manifest.project.name,
        "agent": manifest.agent.name,
        "config": str(config_path),
        "total_tools": len(tools),
        "sources": [
            {
                "id": source.source_id,
                "type": source.source_type,
                "tool_count": len(source.tools),
                "sample_tool": source.tools[0].name if source.tools else None,
                "warnings": source.warnings,
            }
            for source in loaded_sources
        ],
        "api_surface": api_artifacts.surface_summary() if api_artifacts else None,
        "anthropic_surface": (
            anthropic_artifacts.surface_summary() if anthropic_artifacts else None
        ),
        "frameworks": _frameworks_surface(
            adk_artifacts,
            langchain_artifacts,
            crewai_artifacts,
            n8n_artifacts,
            conductor_artifacts,
        ),
        "codex_plugin_surface": (
            codex_plugin_artifacts.surface_summary().model_dump(mode="json")
            if codex_plugin_artifacts
            else None
        ),
        "policy_packs": [pack.model_dump(mode="json") for pack in policy_packs.loaded],
        # v0.20 (PR #111 review follow-up #3): surface third-party
        # adapter discovery results in the doctor payload so the
        # operator can confirm which extensions were loaded (or why
        # they were skipped) without running a full scan.
        "loaded_adapters": loaded_adapters,
        "baseline": _default_baseline_status(base_dir),
        "warnings": warnings,
        "unresolved_sources": unresolved_sources,
        # Where this adoption stands, and the one thing that moves it up
        # (#410 §G). Derived from the manifest and the checkout only, because
        # `doctor` answers before any report exists — see
        # ``core.adoption_ladder``.
        "adoption": {
            "rung": rung.number,
            "name": rung.name,
            "you_get": rung.you_get,
            "exit_criterion": rung.exit_criterion,
            "blocking": list(rung.blocking),
        },
        "manifest_summary": {
            "environment_target": manifest.environment.target,
            "has_permissions": bool(
                manifest.permissions.scopes or manifest.permissions.credential_mode
            ),
            "has_policies": bool(
                manifest.policies.require_approval_for_tools
                or manifest.policies.require_confirmation_for_tools
                or manifest.policies.require_idempotency_for_tools
            ),
            "scope_count": len(manifest.permissions.scopes),
        },
    }
    inspected = redact_data(payload, stats=RedactionStats(), path="$")
    # Not redacted and not part of the published payload shape: raw manifest
    # bytes, returned out of band so the caller derives its placeholders and its
    # identity from the same read this inspection used.
    inspected[MANIFEST_SNAPSHOT_KEY] = manifest_bytes
    return inspected
