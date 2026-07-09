"""n8n adapter — auth / credentials / risk-hint synthesis.

Internal module. Turns ``_NodeItem.credentials`` into normalized
``AuthInfo`` for the emitted ``Tool``, records credential references
on the artifact bag, and emits ``ToolRiskHint`` rows keyed on the
credential type and HTTP method.

The risk-hint vocabulary is deliberately keyword-driven and is the
same surface the existing v0.15 ``provenance_kind`` would mark as
``keyword_heuristic`` for downstream filtering — once a consumer of
``provenance_kind`` exists.
"""

from __future__ import annotations

from typing import Any

from agents_shipgate.core.artifact_models import N8nArtifacts
from agents_shipgate.core.domain import AuthInfo, ToolRiskHint
from agents_shipgate.inputs.n8n._common import (
    _NodeItem,
    _redact_text,
    _string_or_none,
    _tool_node_kind,
    _top_level_string,
)


def _auth_info(item: _NodeItem) -> AuthInfo:
    refs = _credential_refs(item)
    credential_type = refs[0]["type"] if refs else None
    # Credential references identify an authentication mechanism, not an
    # enumerable operation grant. Treat them as known unscoped authority;
    # the artifact bag retains the redacted credential references for review.
    return AuthInfo(
        type=credential_type,
        scopes=[],
        credential_mode="n8n_credential" if refs else None,
        source="n8n_credentials",
        mode="unscoped" if refs else "unknown",
        explicit=bool(refs),
    )


def _credential_refs(item: _NodeItem) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for key, raw in item.credentials.items():
        if isinstance(raw, dict):
            refs.append(
                {
                    "type": _string_or_none(raw.get("type")) or str(key),
                    "id": _string_or_none(raw.get("id")),
                    "name_present": bool(_string_or_none(raw.get("name"))),
                }
            )
        elif isinstance(raw, str):
            refs.append({"type": str(key), "id": raw, "name_present": False})
    return refs


def _record_credentials(
    item: _NodeItem,
    source_path: str,
    workflow_id: str,
    artifacts: N8nArtifacts,
) -> None:
    for ref in _credential_refs(item):
        artifacts.credential_refs.append(
            {
                **ref,
                "source_ref": f"{source_path}#node:{item.node_id}",
                "node_id": item.node_id,
                "node_type": item.node_type,
                "workflow_id": workflow_id,
            }
        )


def _risk_hints(item: _NodeItem, *, method: str | None) -> list[ToolRiskHint]:
    hints: list[ToolRiskHint] = []
    kind = _tool_node_kind(item)
    if kind == "code_tool":
        _add_hint(hints, "code_execution", "high", {"node_type": item.node_type})
    if method and method not in {"GET", "HEAD", "OPTIONS"}:
        _add_hint(hints, "external_write", "medium", {"method": method})
    for ref in _credential_refs(item):
        credential_type = str(ref.get("type") or "").lower()
        if any(token in credential_type for token in ("stripe", "paypal", "billing")):
            _add_hint(hints, "financial_action", "medium", {"credential_type": ref.get("type")})
        if any(
            token in credential_type
            for token in ("gmail", "mail", "slack", "twilio", "sms", "discord")
        ):
            _add_hint(
                hints,
                "customer_communication",
                "medium",
                {"credential_type": ref.get("type")},
            )
        if any(
            token in credential_type for token in ("aws", "azure", "gcp", "kubernetes", "github")
        ):
            _add_hint(
                hints,
                "infrastructure_change",
                "medium",
                {"credential_type": ref.get("type")},
            )
        if any(
            token in credential_type
            for token in ("postgres", "mysql", "database", "sheets", "notion")
        ):
            _add_hint(
                hints,
                "sensitive_data_access",
                "medium",
                {"credential_type": ref.get("type")},
            )
    return hints


def _add_hint(
    hints: list[ToolRiskHint],
    tag: str,
    confidence: str,
    evidence: dict[str, Any],
) -> None:
    if any(hint.tag == tag and hint.confidence == confidence for hint in hints):
        return
    hints.append(
        ToolRiskHint(
            tag=tag,
            source="n8n_static",
            confidence=confidence,
            evidence=evidence,
        )
    )


def _http_path_hint(item: _NodeItem) -> str | None:
    value = _top_level_string(item.parameters, {"url", "path", "endpoint"})
    if not value:
        return None
    if "://" in value:
        value = value.split("://", 1)[1].split("/", 1)[-1]
    return _redact_text(value[:200])
