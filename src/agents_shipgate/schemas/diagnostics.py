from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_serializer,
    model_validator,
)
from pydantic.functional_serializers import SerializerFunctionWrapHandler

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
    * ``executable`` and ``args`` are *computed* from the retargeted string, so
      a caller that would rather not parse a shell string does not have to.
      They are a projection and never independent input — supplying them is an
      ``extra_forbidden`` error, and because they are recomputed on every read
      no mutation path can leave them describing a command the action no longer
      holds. "The two forms cannot disagree" is a property of the type rather
      than a claim about one code path.

    This model is published with ``extra="forbid"``, so the two properties are
    not additive for a strict consumer validating against the pre-v23 shape —
    contract v23 carries the change, and they are omitted entirely rather than
    serialized as ``null`` so that every non-command action stays byte-for-byte
    what it was.
    """

    model_config = ConfigDict(extra="forbid")

    kind: NextActionKind
    command: str | None = None
    path: str | None = None
    why: str
    expects: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def executable(self) -> list[str] | None:
        """Entry-point argv tokens for :attr:`command`, or ``None``.

        Computed rather than stored, which is what makes "the two forms cannot
        disagree" a property of the type instead of a claim about one code
        path. Setting the pair once during validation left it stale after
        ``model_copy(update={"command": ...})`` — which skips validation — and
        after a plain attribute assignment, so a mutated action could serialize
        an argv belonging to the command it used to hold. Deriving on every
        read closes both, and ``extra="forbid"`` now rejects a caller trying to
        supply the pair at all.
        """

        return self._argv[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def args(self) -> list[str] | None:
        """The remaining argv tokens. See :attr:`executable`."""

        return self._argv[1]

    @property
    def _argv(self) -> tuple[list[str], list[str]] | tuple[None, None]:
        if self.kind != "command" or not self.command:
            return (None, None)
        # ``None`` means the string has no faithful argv form — a leading
        # ``NAME=VALUE`` assignment, or unquoted shell syntax. Withholding the
        # pair is the honest answer; the rendered string carries the whole
        # instruction either way.
        return split_invocation(self.command) or (None, None)

    @model_serializer(mode="wrap")
    def _drop_absent_argv(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Emit ``executable``/``args`` only where they carry something.

        Every other optional field on this model serializes as ``null`` when
        unset, and these deliberately do not. A ``review``/``edit``/``stop``
        action can never carry an argv, so emitting the keys there would widen
        the wire shape for every consumer to describe an absence they could
        already infer from ``kind``.
        """

        data = handler(self)
        for field in ("executable", "args"):
            if data.get(field) is None:
                data.pop(field, None)
        return data

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
