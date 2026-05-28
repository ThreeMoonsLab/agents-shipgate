"""Generate synthetic Shipgate scan scenarios for performance benchmarking.

Produces three sizes under ``benchmark/perf/scenarios/``:

- ``small/``  — ~10 tools, single MCP source
- ``medium/`` — ~50 tools, MCP + OpenAPI + manifest policies
- ``large/``  — ~200 tools, MCP × 2 + OpenAPI + policies + risk overrides

Each scenario is **deterministic**: seeded RNG so file content is byte-
identical across runs. This is important because the latency budget
tests assert wallclock under a budget — if the input changed every
run, regression detection would be noisy.

The generated content is designed to **exercise the check engine**:

- A mix of read / write / destructive / financial / external-communication
  tools so risk-tag classification fires.
- A handful of intentionally missing descriptions, broad scopes, and
  scope-coverage gaps so multiple checks emit findings.
- Manifest declarations that match *some* tools (so policy checks pass
  for those) but leave others uncovered (so other policy checks fire).

This isn't a comprehensive load test — it's a CI-shaped microbenchmark.
The goal is: catch regressions where a refactor doubles the scan
latency, not measure absolute throughput.

Usage:

    python benchmark/perf/generate_scenarios.py            # all three
    python benchmark/perf/generate_scenarios.py --size medium

The pytest latency-budget suite reads the committed outputs; rerun this
script only when you intentionally want to change scenario content.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

# Module-level seed: changing this changes every generated tool name,
# which will churn the entire scenario tree. Keep stable across releases
# unless you explicitly want to refresh budgets.
_SEED = 20260527

# Token vocabularies. Each name is built from one verb + one resource.
# The risk tags fire on the verb tokens — `core/risk_hints.py` keyword
# classifier — so the generated tools have a realistic risk distribution.
_READ_VERBS = ("get", "list", "lookup", "search", "view", "status")
_WRITE_VERBS = ("create", "update", "issue", "send", "charge", "refund")
_DESTRUCTIVE_VERBS = ("cancel", "delete", "destroy", "remove")
_RESOURCES = (
    "user",
    "order",
    "invoice",
    "payment",
    "subscription",
    "ticket",
    "thread",
    "message",
    "comment",
    "customer",
    "account",
    "session",
    "project",
    "task",
    "milestone",
    "report",
    "metric",
    "alert",
    "audit",
    "log",
    "key",
    "secret",
    "config",
    "policy",
    "scope",
    "role",
    "team",
    "member",
    "label",
    "tag",
)

# A small set of providers gives scopes some realistic shape.
_PROVIDERS = ("stripe", "zendesk", "shopify", "gmail", "github", "internal")


def _rng() -> random.Random:
    """Return a fresh seeded RNG so generation is deterministic."""
    return random.Random(_SEED)


def _verb_category(rng: random.Random) -> tuple[str, str]:
    """Pick a verb + its category.

    Distribution: 60% read, 30% write, 10% destructive. Roughly matches
    a real agent surface and exercises the keyword classifier across
    all three tiers.
    """
    roll = rng.random()
    if roll < 0.60:
        return rng.choice(_READ_VERBS), "read"
    if roll < 0.90:
        return rng.choice(_WRITE_VERBS), "write"
    return rng.choice(_DESTRUCTIVE_VERBS), "destructive"


def _tool(rng: random.Random, index: int) -> dict[str, Any]:
    """Build one MCP-shaped tool dict."""
    verb, category = _verb_category(rng)
    resource = rng.choice(_RESOURCES)
    provider = rng.choice(_PROVIDERS)
    name = f"{provider}_{verb}_{resource}_{index}"

    # 1 in 12 tools intentionally lacks a description so
    # SHIP-DOC-MISSING-DESCRIPTION fires for a realistic subset.
    has_description = rng.random() > (1 / 12)
    description = (
        f"{verb.capitalize()} a {resource} record via the {provider} integration."
        if has_description
        else None
    )

    # Schema shape: write/destructive tools get an action/payload-style
    # input to trigger broad-free-text and missing-bounds checks
    # occasionally; read tools get a structured id-style input.
    if category == "read":
        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["id"],
        }
    else:
        # 1 in 5 risky tools deliberately lacks a maximum bound on
        # `amount` to fire SHIP-SCHEMA-MISSING-BOUNDS.
        bounded = rng.random() > 0.2
        amount: dict[str, Any] = {"type": "number"}
        if bounded:
            amount["maximum"] = 10000
        input_schema = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "amount": amount,
                "note": {"type": "string"},
            },
            "required": ["id"],
        }

    tool: dict[str, Any] = {
        "name": name,
        "inputSchema": input_schema,
        "outputSchema": {"type": "object"},
    }
    if description is not None:
        tool["description"] = description
    # 1 in 8 tools declares an explicit auth scope; the rest leave it
    # absent so SHIP-AUTH-MISSING-SCOPE fires on writes and
    # SHIP-AUTH-SCOPE-COVERAGE-MISSING fires on the manifest.
    if rng.random() > (7 / 8):
        tool["auth"] = {
            "type": "oauth2",
            "scopes": [f"{provider}:{resource}:write" if category != "read" else f"{provider}:{resource}:read"],
        }
    return tool


def _mcp_payload(rng: random.Random, count: int, *, start_index: int = 0) -> dict[str, Any]:
    """Build an MCP exports JSON payload with ``count`` tools."""
    tools = [_tool(rng, start_index + i) for i in range(count)]
    return {"tools": tools}


def _openapi_payload(rng: random.Random, count: int) -> dict[str, Any]:
    """Build a minimal OpenAPI 3 spec with ``count`` operations.

    Tools come out as ``operationId``-named entries. Operations include
    a security requirement so the OpenAPI loader sees auth metadata.
    """
    paths: dict[str, Any] = {}
    for i in range(count):
        verb, category = _verb_category(rng)
        resource = rng.choice(_RESOURCES)
        op_id = f"api_{verb}_{resource}_{i}"
        method = "get" if category == "read" else "post"
        paths[f"/{resource}/{i}"] = {
            method: {
                "operationId": op_id,
                "summary": f"{verb.capitalize()} {resource}",
                "description": f"{verb.capitalize()} a {resource} via the HTTP API.",
                "security": [{"oauth2": [f"api:{resource}:{'read' if category == 'read' else 'write'}"]}],
                "responses": {"200": {"description": "OK"}},
            }
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": "Benchmark API", "version": "1.0.0"},
        "components": {
            "securitySchemes": {
                "oauth2": {
                    "type": "oauth2",
                    "flows": {"clientCredentials": {"tokenUrl": "https://example.com/oauth/token", "scopes": {}}},
                }
            }
        },
        "paths": paths,
    }


def _manifest(
    *,
    size_label: str,
    sources: list[dict[str, Any]],
    declare_policies: bool,
    scope_count: int,
) -> dict[str, Any]:
    """Build a shipgate.yaml manifest dict for one scenario size.

    Field shapes follow ``schemas/manifest/`` (strict; ``extra="forbid"``)
    — adding fields here without matching the schema yields a config_error.
    """
    manifest: dict[str, Any] = {
        "version": "0.1",
        "project": {"name": f"benchmark-{size_label}"},
        "agent": {
            "name": f"Benchmark {size_label.capitalize()} Agent",
            "declared_purpose": [
                "Process customer support and billing operations.",
            ],
            "prohibited_actions": [
                "execute arbitrary code without approval",
            ],
        },
        "environment": {"target": "production_like"},
        "tool_sources": sources,
    }
    # Permissions block — declares a handful of scopes that cover SOME
    # of the tools' inferred scopes (so SHIP-AUTH-SCOPE-COVERAGE-MISSING
    # fires on the rest).
    manifest["permissions"] = {
        "scopes": [f"{_PROVIDERS[i]}:read" for i in range(min(scope_count, len(_PROVIDERS)))],
    }
    # Policies — declare approval/confirmation for a couple of named
    # tools so policy checks have real work matching against the loaded
    # surface. The tools named here may not exist for every seed; the
    # check engine treats unmatched policy entries as stale-policy
    # findings, which is part of the realistic load.
    if declare_policies:
        manifest["policies"] = {
            "require_approval_for_tools": [
                {"tool": "stripe_refund_payment_0", "reason": "financial_action"}
            ],
            "require_confirmation_for_tools": [
                {"tool": "gmail_send_message_1", "reason": "external_communication"}
            ],
        }
    return manifest


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write a dict as YAML.

    We use a simple inline emitter rather than pulling ruamel.yaml so
    the generator stays import-light. The output isn't pretty, but it's
    deterministic and parses identically.
    """
    import yaml  # pyyaml is already a runtime dependency

    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def generate_small(root: Path) -> None:
    """~10 tools, single MCP source."""
    rng = _rng()
    scenario = root / "small"
    scenario.mkdir(parents=True, exist_ok=True)
    (scenario / "tools").mkdir(exist_ok=True)
    _write_json(scenario / "tools" / "mcp.json", _mcp_payload(rng, 10))
    manifest = _manifest(
        size_label="small",
        sources=[{"id": "main", "type": "mcp", "path": "tools/mcp.json"}],
        declare_policies=False,
        scope_count=2,
    )
    _write_yaml(scenario / "shipgate.yaml", manifest)


def generate_medium(root: Path) -> None:
    """~50 tools, MCP + OpenAPI + manifest policies."""
    rng = _rng()
    scenario = root / "medium"
    scenario.mkdir(parents=True, exist_ok=True)
    (scenario / "tools").mkdir(exist_ok=True)
    (scenario / "specs").mkdir(exist_ok=True)
    _write_json(scenario / "tools" / "mcp.json", _mcp_payload(rng, 30))
    _write_yaml(scenario / "specs" / "api.openapi.yaml", _openapi_payload(rng, 20))
    manifest = _manifest(
        size_label="medium",
        sources=[
            {"id": "mcp", "type": "mcp", "path": "tools/mcp.json"},
            {"id": "openapi", "type": "openapi", "path": "specs/api.openapi.yaml"},
        ],
        declare_policies=True,
        scope_count=4,
    )
    _write_yaml(scenario / "shipgate.yaml", manifest)


def generate_large(root: Path) -> None:
    """~200 tools, multi-source with policies and risk overrides.

    Two MCP files exercise the cross-source dedupe path; the OpenAPI
    source pushes the tool-surface-too-large threshold; the manifest
    declares broader policies so SHIP-MANIFEST-STALE-POLICY can fire on
    a couple of stale entries.
    """
    rng = _rng()
    scenario = root / "large"
    scenario.mkdir(parents=True, exist_ok=True)
    (scenario / "tools").mkdir(exist_ok=True)
    (scenario / "specs").mkdir(exist_ok=True)
    _write_json(scenario / "tools" / "mcp-part-a.json", _mcp_payload(rng, 80, start_index=0))
    _write_json(scenario / "tools" / "mcp-part-b.json", _mcp_payload(rng, 80, start_index=80))
    _write_yaml(scenario / "specs" / "api.openapi.yaml", _openapi_payload(rng, 40))
    manifest = _manifest(
        size_label="large",
        sources=[
            {"id": "mcp-a", "type": "mcp", "path": "tools/mcp-part-a.json"},
            {"id": "mcp-b", "type": "mcp", "path": "tools/mcp-part-b.json"},
            {"id": "openapi", "type": "openapi", "path": "specs/api.openapi.yaml"},
        ],
        declare_policies=True,
        scope_count=6,
    )
    # A couple of stale entries to fire SHIP-MANIFEST-STALE-POLICY.
    manifest["policies"]["require_approval_for_tools"].append(
        {"tool": "nonexistent_tool_stale_a", "reason": "intentionally_stale"}
    )
    manifest["policies"]["require_confirmation_for_tools"].append(
        {"tool": "nonexistent_tool_stale_b", "reason": "intentionally_stale"}
    )
    _write_yaml(scenario / "shipgate.yaml", manifest)


_GENERATORS = {
    "small": generate_small,
    "medium": generate_medium,
    "large": generate_large,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--size",
        choices=["small", "medium", "large", "all"],
        default="all",
        help="Which scenario(s) to (re)generate. Default: all.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent / "scenarios",
        help="Destination root (defaults to benchmark/perf/scenarios/).",
    )
    args = parser.parse_args()

    sizes = ["small", "medium", "large"] if args.size == "all" else [args.size]
    for size in sizes:
        _GENERATORS[size](args.root)
        print(f"  generated {args.root / size}")


if __name__ == "__main__":
    main()
