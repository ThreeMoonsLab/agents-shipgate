"""n8n adapter — package public surface.

Decomposed from a monolithic ``inputs/n8n.py`` (1493 lines) into six
internal modules in v0.21 (E8 follow-through from the round-3
architecture review). The public surface is unchanged:

  - :class:`N8nAdapter` — registered in
    ``agents_shipgate.inputs.protocol.REGISTRY`` as the per-scan
    adapter for the ``n8n`` source type.
  - :func:`load_n8n_artifacts` — the per-scan loader the adapter
    delegates to; also imported directly by ``tests/test_n8n.py``.

Anything else is package-internal (leading-underscore module names).

## Module map

  - ``_adapter.py`` — ``N8nAdapter``, ``load_n8n_artifacts``, auxiliary
    artifact loaders (credential stubs, structured refs, MCP tool
    inventories).
  - ``_workflows.py`` — workflow file loading, shape detection,
    ``_extract_workflow``, connection-graph edges, node-record
    builders, dynamic-surface emission.
  - ``_tools.py`` — Tool extraction from workflow nodes (the four
    flavours: ai/workflow/code/http + mcp_client), tool-name and
    schema helpers, MCP Client Tool selection mode, tool-artifact
    recording.
  - ``_auth_risk.py`` — credential references, ``AuthInfo`` synthesis,
    risk-hint heuristics keyed on credential type and HTTP method,
    HTTP path-hint extraction.
  - ``_secrets.py`` — secret scanning of workflow / node parameters /
    notes / pinData / staticData against the global ``SECRET_PATTERNS``;
    redaction policy is enforced by ``core.privacy``.
  - ``_common.py`` — constants, leaf helpers, redaction shims, node
    graph models (``_NodeItem`` / ``_Edge``), node-kind classification.

## Dependency direction

  _common  →  (used by all)
  _secrets, _auth_risk  →  _common
  _tools  →  _common, _auth_risk
  _workflows  →  _common, _auth_risk, _secrets, _tools
  _adapter  →  _common, _workflows  (orchestrator)

``_workflows`` and ``_tools`` form a mutual call pattern at runtime
(workflows fan out to tools for Tool extraction; tools call back into
workflows for record builders and dynamic-surface emission). The
import edge stays one-way (workflows → tools) at module load; tools
uses late imports inside the functions that need workflow record
builders. This keeps the static import graph a DAG.
"""

from agents_shipgate.inputs.n8n._adapter import N8nAdapter, load_n8n_artifacts

__all__ = ["N8nAdapter", "load_n8n_artifacts"]
