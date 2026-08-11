from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.invocation import retarget_command, split_invocation

NextActionKind = Literal["command", "edit", "review", "stop"]
DiagnosticSeverity = Literal["block", "warn", "info"]


class NextAction(BaseModel):
    """One ranked recovery step.

    Ordered list position is the rank — there is no separate ``rank`` field.

    - ``kind="command"`` → ``command`` is a runnable shell string.
    - ``kind="edit"`` → ``path`` points at a file (optionally
      ``shipgate.yaml:<line>``).
    - ``kind="review"`` → no command, just a sentence in ``why``.
    - ``kind="stop"`` → negative-control; ``command`` is None.

    Call sites write ``command`` as the console-script spelling
    (``agents-shipgate ...``). Two things happen to it here, and both happen
    here rather than at the ~40 construction sites so that no surface can opt
    out of the policy by being written later:

    * It is retargeted to however *this* process entered the CLI. Emitting
      ``agents-shipgate`` from a ``python -m agents_shipgate`` run hands the
      caller a command its environment may have no wrapper for (#322).
    * ``executable`` and ``args`` are derived from the retargeted string, so a
      caller that would rather not parse a shell string does not have to. They
      are a *projection*, never independent input: deriving them from the same
      value the string is rendered from is what makes it impossible for the
      two forms to disagree.
    """

    model_config = ConfigDict(extra="forbid")

    kind: NextActionKind
    command: str | None = None
    path: str | None = None
    why: str
    expects: str | None = None
    executable: list[str] | None = None
    args: list[str] | None = None

    @model_validator(mode="after")
    def _check_kind_fields(self) -> NextAction:
        if self.kind == "command" and not self.command:
            raise ValueError("kind='command' requires a non-empty command")
        if self.kind == "edit" and not self.path:
            raise ValueError("kind='edit' requires a non-empty path")
        if self.kind == "stop" and self.command is not None:
            raise ValueError("kind='stop' must not carry a command")
        if self.kind == "command" and self.command:
            self.command = retarget_command(self.command)
            split = split_invocation(self.command)
            # ``None`` means the string has no faithful argv form — a leading
            # ``NAME=VALUE`` assignment is shell syntax, not an argv token.
            # Leaving the structured pair unset is the honest answer; the
            # rendered string still carries the whole instruction.
            self.executable, self.args = split if split is not None else (None, None)
        else:
            self.executable = None
            self.args = None
        return self

    def to_legacy_string(self) -> str:
        """Project to the back-compat single-string ``next_action`` field."""
        if self.kind == "command":
            assert self.command is not None
            return self.command
        if self.kind == "edit":
            return f"Edit {self.path}"
        if self.kind == "review":
            return f"Review: {self.why}"
        return f"Stop: {self.why}"


class Diagnostic(BaseModel):
    """A first-run failure mode with at least one ranked recovery step."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    severity: DiagnosticSeverity
    next_actions: list[NextAction] = Field(min_length=1)


DIAG_MISSING_MANIFEST = "SHIP-DIAG-MISSING-MANIFEST"
DIAG_INVALID_MANIFEST = "SHIP-DIAG-INVALID-MANIFEST"
DIAG_NO_AGENT_SURFACE = "SHIP-DIAG-NO-AGENT-SURFACE"
DIAG_NON_AGENT_LIBRARY = "SHIP-DIAG-NON-AGENT-LIBRARY"
DIAG_PURE_PROMPT_EXPERIMENT = "SHIP-DIAG-PURE-PROMPT-EXPERIMENT"
DIAG_MCP_OPENAPI_ARTIFACT_ONLY = "SHIP-DIAG-MCP-OPENAPI-ARTIFACT-ONLY"
DIAG_CODEX_PLUGIN_PACKAGE_DETECTED = "SHIP-DIAG-CODEX-PLUGIN-PACKAGE-DETECTED"
DIAG_ZERO_TOOLS = "SHIP-DIAG-ZERO-TOOLS"
DIAG_DYNAMIC_TOOLSETS_ONLY = "SHIP-DIAG-DYNAMIC-TOOLSETS-ONLY"
DIAG_MISSING_SOURCE_FILE = "SHIP-DIAG-MISSING-SOURCE-FILE"
DIAG_CHANGE_ME_PLACEHOLDERS = "SHIP-DIAG-CHANGE-ME-PLACEHOLDERS"
DIAG_NO_PRODUCTION_PERMISSIONS = "SHIP-DIAG-NO-PRODUCTION-PERMISSIONS"
# v0.20 (PR #111 review follow-up): manifest references a
# ``tool_sources[].type`` that resolves to no registered adapter. Two
# common causes: (a) plugin discovery is disabled (env unset or
# ``--no-plugins``) and the source type belongs to a third-party
# adapter; (b) a typo of a built-in name. The diagnostic next_actions
# route the agent to the right remediation depending on which case it
# is — installing the third-party package, enabling plugin discovery,
# or fixing the typo — instead of the legacy "edit shipgate.yaml"
# advice that ``diagnose_invalid_manifest`` would otherwise emit.
DIAG_UNKNOWN_ADAPTER_SOURCE_TYPE = "SHIP-DIAG-UNKNOWN-ADAPTER-SOURCE-TYPE"

ALL_DIAGNOSTIC_IDS: tuple[str, ...] = (
    DIAG_MISSING_MANIFEST,
    DIAG_INVALID_MANIFEST,
    DIAG_NO_AGENT_SURFACE,
    DIAG_NON_AGENT_LIBRARY,
    DIAG_PURE_PROMPT_EXPERIMENT,
    DIAG_MCP_OPENAPI_ARTIFACT_ONLY,
    DIAG_CODEX_PLUGIN_PACKAGE_DETECTED,
    DIAG_ZERO_TOOLS,
    DIAG_DYNAMIC_TOOLSETS_ONLY,
    DIAG_MISSING_SOURCE_FILE,
    DIAG_CHANGE_ME_PLACEHOLDERS,
    DIAG_NO_PRODUCTION_PERMISSIONS,
    DIAG_UNKNOWN_ADAPTER_SOURCE_TYPE,
)
