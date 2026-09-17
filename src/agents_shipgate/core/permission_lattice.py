"""How wide a host permission rule is, and how two of them compare (#657).

Two questions were previously answered by string shape alone, and both
answers were wrong often enough to cost a reviewer's trust:

*Which is wider?* Drift compared rule strings for equality, so replacing
``Bash(npm *)`` with ``Bash(npm test:*)`` — a narrowing — was reported the
same way as replacing it with ``Bash(*)``. The reviewer saw "expanded"
either way.

*How severe?* Every wildcard allow was ``critical`` and ``admin``, so
``Read(**)`` sat beside ``Bash(*)`` at the top of the table. On the
three-widening scenario, four of eight grants were noise of this kind, and
a severity column that cries critical at reading files is one a reviewer
stops reading.

Both answers here are deliberately partial. ``subsumes`` returns ``None``
for any pair it cannot decide soundly, and an undecided pair produces no
direction signal at all — the same silence as before, never a guess. The
pattern language hosts actually use is richer than this lattice; being
quiet about the rest is what makes the part it does answer worth trusting.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What a tool lets an agent reach, which is what decides how much a
#: whole-tool grant matters. Reading files the agent already has checked
#: out is not the same event as running arbitrary commands, and a severity
#: model that cannot tell them apart is noise.
EXECUTION_TOOLS = frozenset({"bash", "shell", "run", "terminal", "execute"})
NETWORK_TOOLS = frozenset({"webfetch", "websearch", "fetch", "browser"})
WRITE_TOOLS = frozenset({"write", "edit", "notebookedit", "multiedit", "patch"})
READ_TOOLS = frozenset({"read", "glob", "grep", "ls", "search", "notebookread"})


@dataclass(frozen=True)
class Rule:
    """One host permission rule, split into the tool and its argument."""

    tool: str
    argument: str | None
    raw: str

    @property
    def whole_tool(self) -> bool:
        """True when the rule grants the tool without narrowing it."""

        return self.argument is None or self.argument.strip() in {"*", "**"}

    @property
    def everything(self) -> bool:
        return self.raw.strip() == "*"


def parse_rule(raw: str) -> Rule:
    """Split ``Tool(argument)`` without interpreting the argument."""

    text = raw.strip()
    if text == "*":
        return Rule(tool="*", argument="*", raw=text)
    open_paren = text.find("(")
    if open_paren == -1:
        return Rule(tool=text, argument=None, raw=text)
    close_paren = text.rfind(")")
    argument = text[open_paren + 1 : close_paren if close_paren > open_paren else len(text)]
    return Rule(tool=text[:open_paren].strip(), argument=argument.strip(), raw=text)


def _same_tool(left: Rule, right: Rule) -> bool:
    return left.tool.strip().lower() == right.tool.strip().lower()


#: The one tool whose rule spelling Claude Code documents two ways (#816).
#: Its permissions page ("Wildcard patterns",
#: https://code.claude.com/docs/en/permissions#wildcard-patterns) says a
#: trailing `:*` is another way to write a trailing ` *`, so `Bash(ls:*)`
#: matches the same commands as `Bash(ls *)`; that the suffix counts only at
#: the end of a pattern; and that a trailing ` *`, when it is the rule's only
#: wildcard, also matches the bare command. Read as text instead, `npm:*` was
#: the prefix `npm:`, which `npm test:*` does not extend, so a narrowing was
#: reported as a new permission. The same page says the space before a
#: trailing `*` is part of the rule, so `Bash(npm run test:*)`, which is
#: `Bash(npm run test *)`, does not match `npm run test:unit`: the text
#: prefix `npm run test:` used to cover it, and no longer does.
#: `WebFetch(domain:*)` is a different syntax whose colon is part of the
#: argument, and stays literal.
_SHELL_TOOL = "bash"


def _is_shell(rule: Rule) -> bool:
    return rule.tool.strip().lower() == _SHELL_TOOL


def _shell_argument(argument: str) -> str | None:
    """A Bash argument in the one spelling the lattice compares (#816).

    ``"npm test:*"`` -> ``"npm test *"``. Anything else is returned as it
    is, including ``"git:* push"``, whose colon the host reads as literal
    text. ``None`` when the suffix is present but the documentation does
    not settle what it covers: nothing before it (``:*``) or whitespace
    right before it (``npm :*``).
    """

    if not argument.endswith(":*"):
        return argument
    command = argument[:-2]
    if not command.strip() or command != command.rstrip():
        return None
    return f"{command} *"


#: Claude Code spells an MCP grant as a bare token: `mcp__<server>` or
#: `mcp__<server>__*` for every tool the server offers, and
#: `mcp__<server>__<tool>` for one tool (its permissions page, "MCP",
#: https://code.claude.com/docs/en/permissions#mcp).
_MCP_PREFIX = "mcp__"


def _mcp_parts(token: str) -> tuple[str, str | None] | None:
    """``mcp__<server>[__<tool>]`` as ``(server, tool)``; ``tool`` is
    ``None`` when the token names no tool segment at all."""

    if not token.startswith(_MCP_PREFIX):
        return None
    server, separator, tool = token[len(_MCP_PREFIX) :].partition("__")
    return server, (tool if separator else None)


def _plain_segment(text: str) -> bool:
    return bool(text) and "*" not in text and _decidable(text)


def names_tools_within_one_mcp_server(rule: str) -> bool:
    """Whether an MCP rule grants less than a whole server (#816).

    ``mcp__github__get_issue`` names one tool and ``mcp__github__get_*``
    a family of them; neither is every target the server offers.
    ``mcp__github`` and ``mcp__github__*`` are the whole server. A token
    whose server is empty or a glob, or whose tool segment is empty or
    opens with ``*``, is not called scoped: under-reading a grant is the
    failure a gate cannot afford.
    """

    parts = _mcp_parts(rule.strip())
    if parts is None:
        return False
    server, tool = parts
    return (
        _plain_segment(server)
        and tool is not None
        and bool(tool)
        and not tool.startswith("*")
        and _decidable(tool)
    )


def _mcp_tool_pattern(token: str) -> str:
    """``mcp__github`` -> ``mcp__github__*``: the two spellings of one
    whole-server grant compare as one pattern. Other tokens are kept."""

    parts = _mcp_parts(token)
    if parts is not None and parts[1] is None and _plain_segment(parts[0]):
        return f"{token}__*"
    return token


def subsumes(wider: str, narrower: str) -> bool | None:
    """Whether every call ``narrower`` allows, ``wider`` allows too.

    ``True`` and ``False`` are claims; ``None`` means this lattice cannot
    decide the pair and no caller may infer a direction from it. The
    decidable cases are the ones a reviewer actually meets:

    * ``*`` allows everything.
    * a bare tool name allows that whole tool.
    * a prefix pattern allows everything sharing its prefix.
    * identical rules allow the same thing, which is not widening.
    * ``Bash(npm:*)`` is ``Bash(npm *)``, and ``mcp__github`` is
      ``mcp__github__*``: two spellings of one grant (#816).
    """

    left, right = parse_rule(wider), parse_rule(narrower)
    if left.raw == right.raw:
        return False
    if left.everything:
        return True
    if right.everything:
        return False
    if not _same_tool(left, right):
        if left.argument is None and right.argument is None:
            # Two bare tool names. Claude Code spells MCP grants this way —
            # `mcp__github__*` against `mcp__github__get_issue` — so without
            # this the single most common real wildcard in a host config is
            # undecidable, and widening to a whole MCP server goes unnamed.
            # The same prefix rule applies; a tool token is just a pattern.
            # A bare `mcp__github` is compared as the `mcp__github__*` it
            # is, or narrowing a whole-server grant to one tool reads as a
            # new grant (#816).
            return _argument_subsumes(
                _mcp_tool_pattern(left.tool.strip()), _mcp_tool_pattern(right.tool.strip())
            )
        # Different tools are incomparable, not equal and not wider. Saying
        # so is a claim this lattice can make soundly.
        return False
    if left.whole_tool:
        return not right.whole_tool
    if right.whole_tool:
        return False
    if _is_shell(left):
        wider_argument = _shell_argument(left.argument or "")
        narrower_argument = _shell_argument(right.argument or "")
        if wider_argument is None or narrower_argument is None:
            return None
        return _argument_subsumes(wider_argument, narrower_argument, shell=True)
    return _argument_subsumes(left.argument or "", right.argument or "")


#: Glob syntax this lattice does not implement. A character class or an
#: optional-character pattern makes `startswith` the wrong question:
#: `a[bc]d` matches `abd` while not being a prefix of it, so treating the
#: text as a literal answers "not wider" about something that is wider.
_UNSUPPORTED_GLOB = frozenset("[]?{}")


def _decidable(text: str) -> bool:
    return not (_UNSUPPORTED_GLOB & set(text))


def _simple_prefix(argument: str) -> str | None:
    """``"npm *"`` -> ``"npm "``; ``None`` when the pattern is not a
    single trailing star over otherwise literal text."""

    if argument.endswith("*") and "*" not in argument[:-1] and _decidable(argument[:-1]):
        return argument[:-1]
    return None


def _argument_subsumes(wider: str, narrower: str, *, shell: bool = False) -> bool | None:
    """Decide prefix patterns and literals; stay quiet about the rest.

    A ``*`` anywhere but the end, or any character class, is left
    undecided, because guessing is how a narrowing gets reported as an
    expansion. Where both sides *are* prefix patterns the answer is
    exact in both directions, which is what lets a genuine narrowing be
    named rather than merely not-misnamed.

    ``shell`` applies the one Bash rule a prefix does not capture: a
    trailing ``" *"`` also matches the bare command, so ``npm test *``
    allows ``npm test``. Between two prefix patterns nothing changes: when
    a narrower ``"x *"`` has a prefix that extends the wider one's, its
    bare command ``x`` extends it too, because the only prefix ``"x "``
    extends and ``"x"`` does not is ``"x "`` itself, and identical
    patterns are decided first.
    """

    if wider == narrower:
        return False
    if not _decidable(wider) or not _decidable(narrower):
        # Not a decline for tidiness: `Bash(a[bc]d)` really does allow
        # `Bash(abd)`, so calling it "not wider" would be a wrong answer
        # rather than a missing one, and a wrong direction is the whole
        # defect this module exists to stop.
        return None
    wider_prefix, narrower_prefix = _simple_prefix(wider), _simple_prefix(narrower)
    if wider_prefix is not None and narrower_prefix is not None:
        # `a*` allows everything `b*` allows exactly when `b` extends `a`.
        return narrower_prefix.startswith(wider_prefix)
    if wider_prefix is not None:
        if "*" in narrower:
            return None
        if shell and wider_prefix.endswith(" ") and narrower == wider_prefix[:-1]:
            # `Bash(npm test *)` and `Bash(npm test:*)` match `npm test`
            # itself, so replacing either with `Bash(npm test)` narrows.
            return True
        # A literal is allowed by the prefix iff it carries it.
        return narrower.startswith(wider_prefix)
    if "*" in wider:
        return None
    # A literal allows only itself, and equality was handled above.
    return False


def tool_class(rule: str) -> str:
    """The class a rule's tool belongs to, for severity."""

    tool = parse_rule(rule).tool.strip().lower()
    if tool == "*":
        return "everything"
    if tool in EXECUTION_TOOLS:
        return "execution"
    if tool in NETWORK_TOOLS:
        return "network"
    if tool in WRITE_TOOLS:
        return "write"
    if tool in READ_TOOLS:
        return "read"
    return "unknown"


def whole_tool_risk(rule: str) -> tuple[str, str]:
    """``(access, risk)`` for an allow rule that grants a whole tool.

    Reading is not writing and writing is not executing, and a table that
    rates them alike is one a reviewer stops reading. The ordering is what
    the grant *reaches*:

    * ``everything`` and ``execution`` — arbitrary commands: ``critical``.
    * ``network`` — the egress half of an exfiltration: ``high``.
    * ``write`` — can change the repository, including its own guardrails:
      ``high``.
    * ``read`` — bounded by a workspace the agent already has checked out.
      Reading it whole is what a coding agent is for, and turning that into
      an exfiltration needs a network or write grant, which is its own row:
      ``low``.
    * ``unknown`` — an unrecognised tool granted whole. Not ``critical``,
      because nothing here establishes that, and not ``low``, because
      nothing establishes that either: ``high``.
    """

    classification = tool_class(rule)
    if classification in {"everything", "execution"}:
        return "admin", "critical"
    if classification in {"network", "write"}:
        return "execute", "high"
    if classification == "read":
        return "read", "low"
    return "execute", "high"


def scoped_risk(rule: str) -> tuple[str, str]:
    """``(access, risk)`` for an allow rule that names a bounded target.

    A scoped rule is the *recommended* form, so rating every one of them
    `high` reproduces the same flatness one level down: a
    well-configured repository would still read as all-high, and the
    column would still carry no information. What survives the narrowing
    is the tool class, so that is what is left.
    """

    classification = tool_class(rule)
    if classification == "read":
        return "read", "low"
    if classification in {"everything", "execution", "network", "write"}:
        return "execute", "medium"
    return "execute", "medium"


__all__ = [
    "Rule",
    "names_tools_within_one_mcp_server",
    "parse_rule",
    "subsumes",
    "tool_class",
    "scoped_risk",
    "whole_tool_risk",
]
