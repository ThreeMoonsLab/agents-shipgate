"""The capability-lock family publishes redacted text and digests of it (#1079).

``capabilities.lock.json``, both lock-diff files and the delta attestations
are built from the lock's facts, so redaction happens where the lock is
assembled (and where a lock is read back), before any digest is taken.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from agents_shipgate.core.capabilities import (
    build_capability_facts,
    capability_authority_hash,
    capability_binding_hash,
    capability_control_hash,
    capability_effect_hash,
    capability_evidence_hash,
    capability_identity_hash,
    capability_risk_hash,
    redacted_capability_fact,
)
from agents_shipgate.core.capability_attestation import project_capability_delta_attestation
from agents_shipgate.core.capability_lock import (
    _lock_hashes,
    _lock_summary,
    build_capability_lock,
    diff_capability_locks,
    load_capability_lock_json,
    render_capability_lock_diff_json,
    render_capability_lock_json,
)
from agents_shipgate.core.domain import Agent, AuthInfo, Tool
from agents_shipgate.report.capability_lock_diff_markdown import (
    render_capability_lock_diff_markdown,
)
from agents_shipgate.schemas.capabilities import (
    CapabilityLockFileV1,
    CapabilityLockSource,
)
from agents_shipgate.schemas.capability_attestation import render_attestation_json
from agents_shipgate.schemas.capability_payload import subject_key
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

REPO_ROOT = Path(__file__).resolve().parent.parent
# Split so no scanner reads a live-looking literal in this file.
_STRIPE = "sk_live_" + "51HxQ2bL9kTq7vR3mN8pZ4wY6aC0dE2fG"
_OPENAI = "sk-" + "proj9Qw3Er5Ty7Ui1Op3As5Df7"
_OPENAI_OTHER = "sk-" + "projZx8Cv6Bn4Ml2Kj0Hg8Fd6"
_GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
_SECRETS = {
    "stripe": _STRIPE,
    "openai": _OPENAI,
    "openai_other": _OPENAI_OTHER,
    "github": _GITHUB,
}
_AGENT = Agent(id="agent:one", name=f"support {_GITHUB}")
_TREE_A = "1" * 40
_TREE_B = "2" * 40


def _manifest(*, project: str = f"lock {_STRIPE}") -> AgentsShipgateManifest:
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": project},
            "agent": {"name": "support-agent", "declared_purpose": ["Support customers."]},
            "environment": {"target": "local"},
            "tool_sources": [
                {"id": "support_api", "type": "openapi", "path": "tools/support.openapi.yaml"}
            ],
        }
    )


def _tool(
    name: str,
    *,
    owner: str | None = None,
    scopes: tuple[str, ...] = ("cases:read",),
    auth_source: str = f"vault {_GITHUB}",
) -> Tool:
    # ``tool:<name>`` ids, so a credential in the name reaches every identity
    # field, as it does for an adapter that keys tools by name.
    return Tool(
        id=f"tool:{name}",
        name=name,
        description=f"{name} test tool",
        source_type="openapi",
        source_id="support_api",
        source_ref=f"tools/support.openapi.yaml#{name}",
        source_path="tools/support.openapi.yaml",
        source_start_line=12,
        source_pointer=f"/paths/{name}",
        owner=owner,
        input_schema={},
        auth=AuthInfo(
            type="oauth",
            credential_mode="delegated",
            source=auth_source,
            scopes=list(scopes),
        ),
        extraction_confidence="high",
    )


def _lock(tools: list[Tool], *, manifest: AgentsShipgateManifest | None = None):
    return build_capability_lock(
        manifest or _manifest(),
        agent=_AGENT,
        tools=tools,
        config_path=Path("shipgate.yaml"),
        manifest_dir=Path("."),
        cli_version="test-version",
        source_count=1,
        source_warning_count=0,
        toolkit_bound_count=0,
        plugins_enabled=True,
    )


def _unredacted_lock(
    tools: list[Tool],
    *,
    project: str = f"lock {_STRIPE}",
    agent_name: str = _AGENT.name,
) -> CapabilityLockFileV1:
    """The lock this engine wrote before #1079: the same facts, unredacted."""

    manifest = _manifest(project=project)
    facts = build_capability_facts(manifest, agent_id=_AGENT.id, tools=tools)
    source = CapabilityLockSource(
        config_path="shipgate.yaml",
        manifest_dir=".",
        project_name=manifest.project.name,
        agent_id=_AGENT.id,
        agent_name=agent_name,
        environment_target="local",
        tool_count=len(tools),
        source_count=1,
        plugins_enabled=True,
    )
    return CapabilityLockFileV1(
        cli_version="test-version",
        source=source,
        summary=_lock_summary(facts),
        hashes=_lock_hashes(source, facts),
        capabilities=facts,
    )


def _secret_tools() -> list[Tool]:
    return [
        _tool(
            f"refunds.lookup {_OPENAI}",
            owner=f"payments {_STRIPE}",
            scopes=("cases:read", f"vault:{_GITHUB}"),
        ),
        _tool("cases.search", owner="support-team"),
    ]


def _leaked(text: str) -> list[str]:
    return sorted(label for label, value in _SECRETS.items() if value in text)


def test_the_lock_publishes_no_credential_and_marks_each_one() -> None:
    clean_fact = _lock([_tool("cases.search", owner="support-team")]).capabilities[0]
    lock = _lock(_secret_tools())
    rendered = render_capability_lock_json(lock)

    assert _leaked(rendered) == []
    payload = json.loads(rendered)
    assert payload["source"]["project_name"] == "lock [REDACTED:stripe_key]"
    assert payload["source"]["agent_name"] == "support [REDACTED:github_token]"
    [fact] = [
        fact for fact in payload["capabilities"] if fact["identity"]["tool_name"] != "cases.search"
    ]
    assert fact["identity"]["tool_name"] == "refunds.lookup [REDACTED:openai_api_key]"
    assert fact["identity"]["tool_id"] == "tool:refunds.lookup [REDACTED:openai_api_key]"
    assert fact["controls"]["evidence_owner"] == "payments [REDACTED:stripe_key]"
    assert fact["authority"]["source"] == "vault [REDACTED:github_token]"
    assert "vault:[REDACTED:github_token]" in fact["authority"]["scopes"]
    assert "vault:[REDACTED:github_token]" in fact["identity"]["scope"]
    assert fact["evidence"]["source_pointer"] == "/paths/refunds.lookup [REDACTED:openai_api_key]"
    # A fact with nothing to redact is published exactly as it was built.
    [clean] = [fact for fact in lock.capabilities if fact.identity.tool_name == "cases.search"]
    assert clean == clean_fact


def test_every_published_digest_describes_the_published_text() -> None:
    rendered = render_capability_lock_json(_lock(_secret_tools()))
    # Read without the loader, which would redact again: these are the bytes.
    published = CapabilityLockFileV1.model_validate_json(rendered)

    for fact in published.capabilities:
        assert fact.id == f"cap_{fact.hashes.identity_hash}"
        assert fact.hashes.identity_hash == capability_identity_hash(fact.identity)
        assert fact.hashes.binding_hash == capability_binding_hash(fact.semantic_assessment)
        assert fact.hashes.effect_hash == capability_effect_hash(fact.effect)
        assert fact.hashes.authority_hash == capability_authority_hash(fact.authority)
        assert fact.hashes.control_hash == capability_control_hash(fact.controls)
        assert fact.hashes.risk_hash == capability_risk_hash(fact.risk_tags)
        assert fact.hashes.evidence_hash == capability_evidence_hash(
            fact.evidence, fact.semantic_assessment
        )
    assert published.hashes == _lock_hashes(published.source, published.capabilities)
    # Reading the published lock back is a fixed point.
    assert render_capability_lock_json(load_capability_lock_json(rendered, source="t")) == rendered


def test_a_lock_written_before_redaction_reads_as_the_fresh_export() -> None:
    tools = _secret_tools()
    old = render_capability_lock_json(_unredacted_lock(tools))
    assert _leaked(old) == ["github", "openai", "stripe"]

    loaded = load_capability_lock_json(old, source="committed lock")
    fresh = _lock(tools)

    # Same ids, digests and text as a fresh export: upgrading moves a
    # credential-bearing capability's id once, in the file, and never shows
    # as a reidentified capability in a comparison.
    assert render_capability_lock_json(loaded) == render_capability_lock_json(fresh)
    diff = diff_capability_locks(loaded, fresh)
    assert diff.summary.model_dump() == {
        "added": 0,
        "removed": 0,
        "reidentified": 0,
        "changed": 0,
        "evidence_changed": 0,
        "unchanged": 2,
        "definition_changed": 0,
    }


def test_a_lock_with_nothing_to_redact_reads_back_byte_for_byte() -> None:
    clean_tool = _tool("cases.search", owner="team", auth_source="manifest")
    old = render_capability_lock_json(
        _unredacted_lock([clean_tool], project="clean", agent_name="support-agent")
    )
    assert _leaked(old) == []

    assert render_capability_lock_json(load_capability_lock_json(old, source="t")) == old


def test_redaction_never_merges_two_capabilities_into_one() -> None:
    # Two tools whose names differ only inside a credential redact to one
    # identity. Both must stay in the lock, under distinct ids.
    first = _tool(f"lookup {_OPENAI}")
    second = _tool(f"lookup {_OPENAI_OTHER}")
    base = _lock([first])
    head = _lock([first, second])

    assert _leaked(render_capability_lock_json(head)) == []
    names = {fact.identity.tool_name for fact in head.capabilities}
    assert names == {"lookup [REDACTED:openai_api_key]"}
    assert len({fact.id for fact in head.capabilities}) == 2
    diff = diff_capability_locks(base, head)
    # The second tool is a row of the diff, not silently folded into the first.
    # The first keeps its source digest only while the two collide, so it
    # reads as reidentified once: over-reporting, the safe direction.
    assert diff.summary.added == 1
    assert diff.summary.removed == 0
    assert diff.summary.reidentified == 1


def test_the_lock_diff_redacts_changed_rows_and_semantic_changes() -> None:
    base = _lock([_tool(f"refunds.lookup {_OPENAI}", owner=f"payments {_STRIPE}")])
    head = _lock([_tool(f"refunds.lookup {_OPENAI}", owner=f"billing {_GITHUB}")])

    diff = diff_capability_locks(base, head, base_path=Path(f"{_OPENAI}/base.json"))
    rendered = render_capability_lock_diff_json(diff)
    markdown = render_capability_lock_diff_markdown(diff)

    assert _leaked(rendered) == []
    assert _leaked(markdown) == []
    payload = json.loads(rendered)
    [row] = payload["changed"]
    assert row["after"]["controls"]["evidence_owner"] == "billing [REDACTED:github_token]"
    assert row["before"]["controls"]["evidence_owner"] == "payments [REDACTED:stripe_key]"
    owner = next(
        change for change in row["semantic_changes"] if change["field"] == "controls.evidence_owner"
    )
    assert owner["after"] == "billing [REDACTED:github_token]"
    assert owner["before"] == "payments [REDACTED:stripe_key]"
    assert payload["base"]["path"] == "[REDACTED:openai_api_key]/base.json"


def test_the_delta_attestation_publishes_no_credential_and_its_keys_recompute() -> None:
    base = _lock([_tool("cases.search", owner="support-team")])
    head = _lock(_secret_tools())

    for version in (1, 2):
        attestation = project_capability_delta_attestation(
            base.capabilities,
            head.capabilities,
            subject_name=f"local:{_GITHUB}",
            base_tree_sha=_TREE_A,
            head_tree_sha=_TREE_B,
            head_commit_sha=None,
            version=version,  # type: ignore[arg-type]
        )
        rendered = render_attestation_json(attestation)
        assert _leaked(rendered) == [], version
        document = json.loads(rendered)
        assert document["subject"][0]["name"] == "local:[REDACTED:github_token]"
        for row in document["predicate"]["delta"]["subjects"]:
            subject = row["subject"]
            # The key is a digest of the published fields, so a consumer
            # recomputes it from the redacted text.
            assert subject["key"] == subject_key(
                agent=subject["agent"], provider=subject["provider"], tool_id=subject["tool_id"]
            )
        # The stdlib reference consumer recomputes keys, set digests and
        # transitions from the published fields alone.
        assert _reference_verifier_problems(document) == [], version


def test_redacting_a_redacted_fact_is_a_no_op() -> None:
    for fact in _lock(_secret_tools()).capabilities:
        assert redacted_capability_fact(fact) is fact


def _reference_verifier_problems(document: dict[str, Any]) -> list[tuple[str, str]]:
    spec = importlib.util.spec_from_file_location(
        "_ref_verifier_1079", REPO_ROOT / "tools" / "verify-capability-delta.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.verify(document).rows)
