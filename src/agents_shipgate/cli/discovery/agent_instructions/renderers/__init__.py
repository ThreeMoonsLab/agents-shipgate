"""Per-target renderers for ``--agent-instructions``.

Each renderer returns a string. Managed-block targets return only the
*inner* content (what goes between the markers); the cursor renderer
returns the full file body since we own the whole file; the Codex skill
renderer returns a repo-scoped file tree.

Managed-block content is sourced from ``docs/target-repo-agent-snippets.md``.
File-tree skill bundles are sourced from packaged ``adoption-kits/`` files.
Snapshot tests enforce parity so authored content and renderers cannot drift
independently.
"""

from __future__ import annotations

from agents_shipgate.cli.discovery.agent_instructions.renderers.agents_md import (
    render_block as render_agents_md,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.claude_code_skill import (
    PRIOR_RENDER_SHA256 as CLAUDE_CODE_SKILL_PRIOR_RENDER_SHA256,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.claude_code_skill import (
    render_bundle_text as render_claude_code_skill_bundle_text,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.claude_code_skill import (
    render_files as render_claude_code_skill_files,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.claude_command import (
    PRIOR_RENDER_SHA256 as CLAUDE_COMMAND_PRIOR_RENDER_SHA256,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.claude_command import (
    render_file as render_claude_command_file,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.claude_md import (
    render_block as render_claude_md,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.codex_skill import (
    PRIOR_RENDER_SHA256 as CODEX_SKILL_PRIOR_RENDER_SHA256,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.codex_skill import (
    render_bundle_text as render_codex_skill_bundle_text,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.codex_skill import (
    render_files as render_codex_skill_files,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.cursor import (
    PRIOR_RENDER_SHA256 as CURSOR_PRIOR_RENDER_SHA256,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.cursor import (
    render_file as render_cursor_file,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.local_contract import (
    PRIOR_RENDER_SHA256 as LOCAL_CONTRACT_PRIOR_RENDER_SHA256,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.local_contract import (
    render_file as render_local_contract_file,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers.pr_template import (
    render_block as render_pr_template,
)

__all__ = [
    "CLAUDE_CODE_SKILL_PRIOR_RENDER_SHA256",
    "CLAUDE_COMMAND_PRIOR_RENDER_SHA256",
    "CODEX_SKILL_PRIOR_RENDER_SHA256",
    "CURSOR_PRIOR_RENDER_SHA256",
    "LOCAL_CONTRACT_PRIOR_RENDER_SHA256",
    "render_agents_md",
    "render_claude_code_skill_bundle_text",
    "render_claude_code_skill_files",
    "render_claude_command_file",
    "render_claude_md",
    "render_codex_skill_bundle_text",
    "render_codex_skill_files",
    "render_cursor_file",
    "render_local_contract_file",
    "render_pr_template",
]
