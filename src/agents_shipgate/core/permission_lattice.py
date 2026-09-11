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


def subsumes(wider: str, narrower: str) -> bool | None:
    """Whether every call ``narrower`` allows, ``wider`` allows too.

    ``True`` and ``False`` are claims; ``None`` means this lattice cannot
    decide the pair and no caller may infer a direction from it. The
    decidable cases are the ones a reviewer actually meets:

    * ``*`` allows everything.
    * a bare tool name allows that whole tool.
    * a prefix pattern allows everything sharing its prefix.
    * identical rules allow the same thing, which is not widening.
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
            return _argument_subsumes(left.tool.strip(), right.tool.strip())
        # Different tools are incomparable, not equal and not wider. Saying
        # so is a claim this lattice can make soundly.
        return False
    if left.whole_tool:
        return not right.whole_tool
    if right.whole_tool:
        return False
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


def _argument_subsumes(wider: str, narrower: str) -> bool | None:
    """Decide prefix patterns and literals; stay quiet about the rest.

    A ``*`` anywhere but the end, or any character class, is left
    undecided, because guessing is how a narrowing gets reported as an
    expansion. Where both sides *are* prefix patterns the answer is
    exact in both directions, which is what lets a genuine narrowing be
    named rather than merely not-misnamed.
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
    "parse_rule",
    "subsumes",
    "tool_class",
    "scoped_risk",
    "whole_tool_risk",
]
