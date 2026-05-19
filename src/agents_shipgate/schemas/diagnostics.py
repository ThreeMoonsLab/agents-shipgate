from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    """

    model_config = ConfigDict(extra="forbid")

    kind: NextActionKind
    command: str | None = None
    path: str | None = None
    why: str
    expects: str | None = None

    @model_validator(mode="after")
    def _check_kind_fields(self) -> NextAction:
        if self.kind == "command" and not self.command:
            raise ValueError("kind='command' requires a non-empty command")
        if self.kind == "edit" and not self.path:
            raise ValueError("kind='edit' requires a non-empty path")
        if self.kind == "stop" and self.command is not None:
            raise ValueError("kind='stop' must not carry a command")
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
)
