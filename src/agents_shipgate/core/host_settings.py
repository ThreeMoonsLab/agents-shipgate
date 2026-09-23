"""One rating for each Claude Code setting the host readers model (#827).

`audit --host` publishes these settings as `permission_mode` grants, the rows
of `diff`, `verify` and `check` take their severity from those grants, and
`check` raises a violation when a change sets one. Those three used to rate the
same value separately, and disagreed: `enableAllProjectMcpServers: true` was a
`critical` grant and row but a `medium` "could not be parsed" violation, and
`defaultMode: dontAsk` was a `medium` row but a blocking `critical` violation.
Every one of them now reads the rating here, so a value has one rating on
every surface:

* the grant's ``access`` and ``risk`` (``core.host_grants``);
* a row's severity, which is the grant's ``risk``, and its ``why``, which is
  the ``basis`` below (``core.capability_diff_rows``);
* the violation ``check`` raises (``core.host_boundary``): a ``critical`` value
  raises ``HOST-PERMISSION-WILDCARD-ALLOW``, any other value
  ``HOST-PERMISSION-ALLOW-EXPANDED``, and the violation carries this ``risk``.
  ``verify`` turns it into a finding of the same severity.

**Basis.** Each value is rated by what Claude Code documents it to do, in its
settings reference (code.claude.com/docs/en/settings) and its table of
permission modes, and the ``basis`` states that in a reviewer's words:

* ``critical`` — the value removes a prompt wholesale: ``bypassPermissions``
  skips every permission prompt, ``skipDangerousModePermissionPrompt: true``
  the confirmation before that mode starts, and
  ``enableAllProjectMcpServers: true`` the approval of each project MCP
  server.
* ``high`` — the value lets a class of action run without a prompt
  (``acceptEdits``, ``auto``), approves one named project MCP server
  (an ``enabledMcpjsonServers`` entry), or is a mode, or a list entry, of a
  shape Claude Code does not document, whose effect is therefore not
  established.
* ``medium`` — every other documented value: it changes which prompts, hooks
  or rules apply without letting anything run without a prompt that an allow
  rule does not already permit. ``dontAsk`` is one of these. Its prompts are
  gone, but Claude Code documents that it denies what no allow rule permits
  rather than running it, which is why this repository's own rater harness
  sets it to confine a session (``benchmark/safety-qualification``).
  ``plan`` and ``default`` are the others, with each restriction a setting
  imposes (``disableBypassPermissionsMode``, ``disableAllHooks``, the two
  managed-only switches) and ``false`` for the two prompt switches.

A rating is of the value, not of the change: whether one value is wider than
the one it replaced is not modelled, so every value a change sets is reviewed.
A value that moves between ``permissions`` and the top level is one a change
sets, because Claude Code documents each setting in one of them.
Removing a setting raises nothing of its own here, as removing ``defaultMode``
never did; the settings file is still a protected surface, and a removal is
still a row.

The rating is also a floor for policy, never a ceiling: a host-boundary policy
that raises ``HOST-PERMISSION-ALLOW-EXPANDED`` or
``HOST-PERMISSION-WILDCARD-ALLOW`` above its engine default raises these
violations with it.

Static-only: this module parses nothing and imports nothing from the readers,
so both of them, and the row projection, can import it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: Scalar settings. Read from ``permissions`` when set there and from the top
#: level otherwise, the way the grant reader always has.
CLAUDE_SCALAR_SETTINGS: tuple[str, ...] = (
    "defaultMode",
    "disableBypassPermissionsMode",
    "allowManagedPermissionRulesOnly",
    "allowManagedHooksOnly",
    "skipDangerousModePermissionPrompt",
    "enableAllProjectMcpServers",
    "disableAllHooks",
)
#: List settings, read from the top level: one grant, one row and one
#: violation per distinct entry, so approving one more server is one change.
#: An entry that names no server is kept whole rather than dropped.
CLAUDE_LIST_SETTINGS: tuple[str, ...] = ("enabledMcpjsonServers",)
#: Settings whose documented value is ``true`` or ``false``.
CLAUDE_BOOLEAN_SETTINGS: frozenset[str] = frozenset({
    "allowManagedPermissionRulesOnly",
    "allowManagedHooksOnly",
    "skipDangerousModePermissionPrompt",
    "enableAllProjectMcpServers",
    "disableAllHooks",
})


@dataclass(frozen=True)
class SettingRating:
    """How much one setting value grants, on every surface that names it."""

    #: The grant's ``access``.
    access: str
    #: The grant's ``risk``, the row's severity and the violation's risk level.
    risk: str
    #: What the value does, stated as what it permits. ``None`` for a value
    #: Claude Code does not document for this setting, where nothing more
    #: specific than "the setting changed" is established.
    basis: str | None


@dataclass(frozen=True)
class SettingValue:
    """One modelled setting value as a settings file declares it."""

    setting: str
    value: Any
    #: ``permissions`` or ``top_level``: where the value was read.
    container: str
    #: Whether this is one entry of a list setting rather than its whole value.
    entry: bool = False

    @property
    def key(self) -> str:
        """What two sides of a change compare: the setting, or one list entry."""

        if self.setting in CLAUDE_LIST_SETTINGS and (self.entry or isinstance(self.value, str)):
            return f"{self.setting}:{_entry_text(self.value)}"
        return self.setting


def _entry_text(value: Any) -> str:
    """One list entry as text: a string itself, anything else as canonical JSON."""

    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _is_server_name(value: Any) -> bool:
    """Whether a list entry names a server as Claude Code documents one: a non-blank string."""

    return isinstance(value, str) and bool(value.strip())


def claude_setting_values(data: Any) -> list[SettingValue]:
    """Every modelled setting value in one Claude Code settings document."""

    if not isinstance(data, dict):
        return []
    permissions = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    values: list[SettingValue] = []
    for setting in CLAUDE_SCALAR_SETTINGS:
        if setting in permissions:
            values.append(SettingValue(setting, permissions[setting], "permissions"))
        elif setting in data:
            values.append(SettingValue(setting, data[setting], "top_level"))
    for setting in CLAUDE_LIST_SETTINGS:
        if setting not in data:
            continue
        value = data[setting]
        if isinstance(value, list):
            # One value per distinct entry. An entry that names no server (an
            # object, a number, a blank string) is kept whole and rated as an
            # approval, as a value of the wrong shape is below: dropping it
            # left no record of it anywhere.
            entries: dict[str, Any] = {}
            for entry in value:
                entries.setdefault(_entry_text(entry), entry)
            values.extend(
                SettingValue(setting, entries[text], "top_level", entry=True)
                for text in sorted(entries)
            )
        else:
            # Not the documented shape: kept whole, and rated as an approval,
            # so an unexpected value is reviewed rather than dropped.
            values.append(SettingValue(setting, value, "top_level"))
    return values


def setting_value_text(setting: str, value: Any) -> str:
    """A value as the settings file spells it: ``true``, ``dontAsk``, ``["a"]``.

    A string is printed bare, except where the setting's documented value is a
    boolean: there ``"false"`` is quoted, so it is not read as ``false``. A
    blank list entry is quoted too, so it reads as an entry and not as nothing.
    """

    if isinstance(value, str):
        quoted = setting in CLAUDE_BOOLEAN_SETTINGS or (
            setting in CLAUDE_LIST_SETTINGS and not _is_server_name(value)
        )
        return json.dumps(value) if quoted else value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


_DEFAULT_MODES: dict[str, SettingRating] = {
    "bypassPermissions": SettingRating(
        "admin", "critical", "skips every permission prompt, so any tool call runs without one"
    ),
    "auto": SettingRating(
        "admin", "high",
        "lets Claude Code approve tool calls itself instead of prompting",
    ),
    "acceptEdits": SettingRating("write", "high", "accepts file edits without a prompt"),
    "dontAsk": SettingRating(
        "unknown", "medium",
        "denies every tool call no allow rule already permits, instead of prompting",
    ),
    "plan": SettingRating(
        "unknown", "medium", "limits the agent to reading and planning, without editing files or running commands"
    ),
    "default": SettingRating("unknown", "medium", "prompts on the first use of each tool"),
}
_UNDOCUMENTED_MODE = SettingRating(
    "unknown", "high",
    "sets a permission mode Claude Code does not document, so what it permits is not established",
)

#: ``(true basis, false basis)`` for the switches that grant nothing either way.
_SWITCHES: dict[str, tuple[str, str]] = {
    "allowManagedPermissionRulesOnly": (
        "admits only managed permission rules, and takes effect only from managed settings",
        "admits permission rules from every settings file",
    ),
    "allowManagedHooksOnly": (
        "admits only managed hooks, and takes effect only from managed settings",
        "admits hooks from every settings file",
    ),
    "disableAllHooks": (
        "turns off every hook, including any that guard the agent's actions",
        "leaves hooks on",
    ),
}
#: ``(true rating, false basis)`` for the switches that take a prompt away.
_PROMPT_SWITCHES: dict[str, tuple[SettingRating, str]] = {
    "skipDangerousModePermissionPrompt": (
        SettingRating(
            "admin", "critical", "skips the confirmation Claude Code asks before bypassPermissions mode starts"
        ),
        "keeps the confirmation Claude Code asks before bypassPermissions mode starts",
    ),
    "enableAllProjectMcpServers": (
        SettingRating(
            "admin", "critical",
            "approves every MCP server the project's .mcp.json declares, without a prompt",
        ),
        "leaves each MCP server the project's .mcp.json declares to be approved on its own",
    ),
}
_UNKNOWN = SettingRating("unknown", "medium", None)


def rate_claude_setting(setting: str, value: Any) -> SettingRating:
    """The one rating a modelled Claude Code setting value carries.

    A switch is truthy the way the reader has always read it (``bool``), so a
    value Claude Code would not accept is rated as the value it most resembles
    and never below it; only its ``basis`` is withheld.
    """

    if setting == "defaultMode":
        if isinstance(value, str):
            return _DEFAULT_MODES.get(value, _UNDOCUMENTED_MODE)
        return _UNDOCUMENTED_MODE
    if setting == "disableBypassPermissionsMode":
        if value == "disable":
            return SettingRating("unknown", "medium", "prevents bypassPermissions mode from being used")
        return _UNKNOWN
    if setting in _PROMPT_SWITCHES:
        rating, off = _PROMPT_SWITCHES[setting]
        if bool(value):
            return rating if value is True else SettingRating(rating.access, rating.risk, None)
        return SettingRating("unknown", "medium", off if value is False else None)
    if setting in _SWITCHES:
        on, off = _SWITCHES[setting]
        documented = {True: on, False: off}.get(value) if isinstance(value, bool) else None
        return SettingRating("unknown", "medium", documented)
    if setting in CLAUDE_LIST_SETTINGS:
        return SettingRating(
            "external", "high",
            "approves this MCP server from the project's .mcp.json, without a prompt"
            if _is_server_name(value)
            else None,
        )
    return _UNKNOWN


def read_at_top_level(data: Any, key: str) -> bool:
    """Whether the reader reads top-level ``key`` of ``data`` as a modelled setting.

    A list setting always is, even when its list is empty. A scalar setting is
    unless ``permissions`` sets it too, where it is read instead; that
    top-level copy is read by nothing, so it stays an unknown key.
    """

    if key in CLAUDE_LIST_SETTINGS:
        return True
    if key not in CLAUDE_SCALAR_SETTINGS:
        return False
    permissions = data.get("permissions") if isinstance(data, dict) else None
    return not (isinstance(permissions, dict) and key in permissions)


__all__ = [
    "CLAUDE_BOOLEAN_SETTINGS",
    "CLAUDE_LIST_SETTINGS",
    "CLAUDE_SCALAR_SETTINGS",
    "SettingRating",
    "SettingValue",
    "claude_setting_values",
    "rate_claude_setting",
    "read_at_top_level",
    "setting_value_text",
]
