"""#657: which way did a permission rule move, and is it worth a warning.

Two defects, one cause. Grants are keyed by rule text, so replacing
`Bash(npm *)` with `Bash(npm test:*)` arrives as a removal plus an
addition — byte-for-byte the same shape as replacing it with `Bash(*)`.
Set arithmetic cannot tell a tightening from a widening, so drift called
both an expansion. And every wildcard allow was rated `critical`, so
`Read(**)` sat beside `Bash(*)` at the top of the table.

The fixture table below is the guard the issue asked for: twenty
widen/narrow/unchanged pairs, each with the direction a reviewer would
assign, replayed through the real reader for both hosts that have a rule
vocabulary. Two pairs are marked `decided=False` on purpose — they are
cases this lattice declines. They are in the table so that "we do not
answer this" stays a tested property rather than an accident, and so the
boundary moves visibly if someone widens it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from agents_shipgate.core.host_grants import (
    HostStaticParseCache,
    build_host_boundary_snapshot,
    build_host_drift_payload,
    build_host_grants_baseline,
    host_audit_inventory,
)
from agents_shipgate.core.permission_lattice import (
    scoped_risk,
    subsumes,
    whole_tool_risk,
)


@dataclass(frozen=True)
class Pair:
    """One before/after replacement and the direction a reviewer sees.

    `truth` is the human reading. `decided` is whether this lattice is
    expected to reach it — a widening it declines still has to *surface*
    (the add signal does that), it just cannot be named.
    """

    before: tuple[str, ...]
    after: tuple[str, ...]
    truth: str
    decided: bool = True

    @property
    def id(self) -> str:
        return f"{self.truth}-{'+'.join(self.before)}-to-{'+'.join(self.after)}"


def _pair(before: str, after: str, truth: str, decided: bool = True) -> Pair:
    return Pair((before,), (after,), truth, decided)


#: Twenty pairs. The vocabulary is shared by both hosts, which is the
#: point of running the same table through each: the direction of a
#: change must not depend on which file the rule was read from.
PAIRS: tuple[Pair, ...] = (
    # Widenings the lattice names.
    _pair("Bash(npm test:*)", "Bash(npm *)", "widen"),
    _pair("Bash(npm *)", "Bash(*)", "widen"),
    _pair("Read(src/**)", "Read(**)", "widen"),
    _pair("WebFetch(domain:example.com)", "WebFetch(domain:*)", "widen"),
    _pair("Edit(src/*)", "Edit(*)", "widen"),
    _pair("Bash(git log:*)", "Bash(git *)", "widen"),
    _pair("Read(src/app.py)", "Read(src/*)", "widen"),
    _pair("Bash(pytest tests/unit)", "Bash(pytest *)", "widen"),
    _pair("Bash(a[bc]*)", "Bash(*)", "widen"),
    _pair("mcp__github__get_issue", "mcp__github__*", "widen"),
    # Narrowings. Each of these was reported as an expansion before #657.
    _pair("Bash(npm *)", "Bash(npm test:*)", "narrow"),
    _pair("Bash(*)", "Bash(npm *)", "narrow"),
    _pair("Read(**)", "Read(src/**)", "narrow"),
    _pair("WebFetch(domain:*)", "WebFetch(domain:example.com)", "narrow"),
    _pair("Edit(*)", "Edit(src/*)", "narrow"),
    _pair("Bash(git *)", "Bash(git log:*)", "narrow"),
    _pair("mcp__github__*", "mcp__github__get_issue", "narrow"),
    # A star that is not a trailing star. `Bash(*.py)` -> `Bash(test_*.py)`
    # is a narrowing, but deciding it means implementing glob containment,
    # and a wrong answer here is exactly the failure #657 is about. The
    # lattice declines; the addition still surfaces.
    _pair("Bash(*.py)", "Bash(test_*.py)", "narrow", decided=False),
    # Unchanged: identical, and reordered. Neither is a change at all.
    Pair(("Bash(npm *)",), ("Bash(npm *)",), "unchanged"),
    Pair(
        ("Read(**)", "Bash(pytest *)"),
        ("Bash(pytest *)", "Read(**)"),
        "unchanged",
    ),
)


def test_the_table_covers_what_the_issue_asked_for() -> None:
    """Twenty pairs, all three directions, and the declines are few."""

    assert len(PAIRS) == 20
    assert {pair.truth for pair in PAIRS} == {"widen", "narrow", "unchanged"}
    assert sum(1 for pair in PAIRS if not pair.decided) == 1
    assert len({pair.id for pair in PAIRS}) == 20


def _claude_inventory(root: Path, rules: tuple[str, ...]) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": list(rules)}}), encoding="utf-8"
    )
    return build_host_boundary_snapshot(root, cache=HostStaticParseCache()).inventory


def _cursor_inventory(
    home: Path, workspace: Path, rules: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> dict:
    (home / ".cursor").mkdir(parents=True, exist_ok=True)
    (home / ".cursor" / "cli-config.json").write_text(
        json.dumps({"permissions": {"allow": list(rules)}}), encoding="utf-8"
    )
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return host_audit_inventory(workspace, scope="local_static")


def _drift(before_inventory: dict, after_inventory: dict) -> dict:
    return build_host_drift_payload(
        baseline=build_host_grants_baseline(before_inventory),
        inventory=after_inventory,
        baseline_file="baseline.json",
    )


def _assert_direction(payload: dict, pair: Pair) -> None:
    """The three acceptance criteria, on one replayed pair."""

    signals = payload["expansion_signals"]
    named = [item for item in signals if item.startswith("permission_widened:")]
    about_after = [
        item for item in signals if any(rule in item for rule in pair.after)
    ]

    if pair.truth == "unchanged":
        # `has_drift` can still be true: reordering the allow list changes
        # the file's bytes, and the audit says so through `artifact_changes`.
        # What must not move is the capability reading — no grant changed,
        # and nothing is worth a warning.
        assert payload["changes"] == [], f"{pair.id}: {payload['changes']}"
        assert not signals, f"{pair.id}: {signals}"
        return

    # No wrong direction is ever asserted, whatever the truth.
    assert (named != []) == (pair.truth == "widen" and pair.decided), (
        f"{pair.id}: permission_widened={named}"
    )

    if pair.truth == "widen":
        # Zero missed widenings: named or not, it must reach the reader.
        assert about_after, f"{pair.id}: widening produced no signal: {signals}"
    elif pair.decided:
        # Zero false expansions: this list is what `preflight` prints as
        # "Expansion signals" and what the drift markdown flags with a ⚠.
        assert not about_after, (
            f"{pair.id}: narrowing reported as an expansion: {about_after}"
        )


@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: pair.id)
def test_direction_on_claude_code(tmp_path: Path, pair: Pair) -> None:
    before = _claude_inventory(tmp_path / "before", pair.before)
    after = _claude_inventory(tmp_path / "after", pair.after)

    _assert_direction(_drift(before, after), pair)


@pytest.mark.parametrize("pair", PAIRS, ids=lambda pair: pair.id)
def test_direction_on_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pair: Pair
) -> None:
    workspace = tmp_path / "repo"
    before = _cursor_inventory(tmp_path / "home-before", workspace, pair.before, monkeypatch)
    after = _cursor_inventory(tmp_path / "home-after", workspace, pair.after, monkeypatch)

    _assert_direction(_drift(before, after), pair)


def test_a_narrowing_is_still_visible_as_a_change(tmp_path: Path) -> None:
    """Suppressing the expansion signal must not suppress the fact.

    The narrowing is not hidden — it is in `changes` as a removal and an
    addition. Only the ⚠ is withheld, and only because the list carrying
    it is one both readers label "Expansion signals".
    """

    before = _claude_inventory(tmp_path / "before", ("Bash(npm *)",))
    after = _claude_inventory(tmp_path / "after", ("Bash(npm test:*)",))

    payload = _drift(before, after)

    assert payload["has_drift"]
    rendered = json.dumps(payload["changes"])
    assert "Bash(npm *)" in rendered and "Bash(npm test:*)" in rendered
    assert payload["expansion_signals"] == []


def test_several_rules_changing_at_once_claims_no_direction(tmp_path: Path) -> None:
    """Two out and two in is no evidence about which replaced which.

    Pairing them by position would be inventing the direction, so the
    lattice is not consulted and the add signals stand alone.
    """

    before = _claude_inventory(tmp_path / "before", ("Bash(npm *)", "Read(src/**)"))
    after = _claude_inventory(tmp_path / "after", ("Bash(npm test:*)", "Read(**)"))

    signals = _drift(before, after)["expansion_signals"]

    assert not [item for item in signals if item.startswith("permission_widened:")]
    assert any("Read(**)" in item for item in signals)


#: The configuration #657 measured the noise on: an ordinary, carefully
#: written host config. Nothing here is a finding, so nothing here should
#: be rated as one.
WELL_CONFIGURED = (
    "Read(**)",
    "Glob(**)",
    "Grep(**)",
    "Bash(pytest *)",
    "Bash(git status:*)",
    "Edit(src/**)",
)


def test_a_well_configured_repository_raises_nothing_above_low(
    tmp_path: Path,
) -> None:
    """The headline metric, measured on the grants a reviewer actually sees.

    Before #657 every wildcard allow was `critical`, so three of these six
    rules — the read tools — were rated the same as `Bash(*)`. A severity
    column that cries critical at reading files is one a reviewer stops
    reading, and stopping is how a real widening gets missed.
    """

    inventory = _claude_inventory(tmp_path / "repo", WELL_CONFIGURED)
    rules = {
        grant["rule"]: (grant["access"], grant["risk"])
        for grant in inventory["grants"]
        if grant["kind"] == "permission_rule"
    }

    assert rules["Read(**)"] == ("read", "low")
    assert rules["Glob(**)"] == ("read", "low")
    assert rules["Grep(**)"] == ("read", "low")
    assert not [rule for rule, (_, risk) in rules.items() if risk == "critical"], (
        f"nothing in a well-configured repository is critical: {rules}"
    )


def test_the_grants_that_should_alarm_still_do(tmp_path: Path) -> None:
    """Quieting the column must not quiet the thing it exists to say."""

    inventory = _claude_inventory(
        tmp_path / "repo", ("Bash(*)", "Read(**)", "WebFetch(*)")
    )
    rules = {
        grant["rule"]: grant["risk"]
        for grant in inventory["grants"]
        if grant["kind"] == "permission_rule"
    }

    assert rules["Bash(*)"] == "critical"
    assert rules["WebFetch(*)"] == "high"
    assert rules["Read(**)"] == "low"


class TestTheGateAgrees:
    """The audit table was not the only place this was decided.

    `core/host_boundary.py` classified wildcard allows a second time, and
    that copy is the one wired to the release decision — so adding
    `Read(**)` did not merely look alarming in a table, it blocked the
    release at `critical`. Both now read the same lattice; these pin that
    they keep agreeing, which is the part that rots.
    """

    @pytest.mark.parametrize(
        "rule", ["Read(**)", "Read", "Glob(**)", "Grep(**)", "Bash(npm test:*)"]
    )
    def test_reading_the_workspace_is_reviewed_not_blocked(self, rule: str) -> None:
        from agents_shipgate.core.host_boundary import _allow_rule_id

        assert _allow_rule_id(rule) == "HOST-PERMISSION-ALLOW-EXPANDED"

    @pytest.mark.parametrize(
        "rule", ["*", "Bash(*)", "Shell", "WebFetch(*)", "Write(*)", "mcp__x__*"]
    )
    def test_unbounded_authority_still_blocks(self, rule: str) -> None:
        from agents_shipgate.core.host_boundary import _allow_rule_id

        assert _allow_rule_id(rule) == "HOST-PERMISSION-WILDCARD-ALLOW"

    def test_a_read_only_wildcard_no_longer_blocks_the_release(
        self, tmp_path: Path
    ) -> None:
        """Driven through the real evaluator, not the helper.

        This is the finding a repository got for writing the ordinary
        coding-agent config: `blocks_release`, `critical`. It is the
        reason the gate could not be left on.
        """

        from agents_shipgate.core.host_boundary import evaluate_host_boundary

        settings = json.dumps({"permissions": {"allow": ["Read(**)", "Grep(**)"]}})
        lines = settings.splitlines()
        diff = (
            "diff --git a/.claude/settings.json b/.claude/settings.json\n"
            "new file mode 100644\n"
            "index 0000000..1111111\n"
            "--- /dev/null\n"
            "+++ b/.claude/settings.json\n"
            f"@@ -0,0 +1,{len(lines)} @@\n"
            + "\n".join(f"+{line}" for line in lines)
            + "\n"
        )

        violations, _ = evaluate_host_boundary(workspace=tmp_path, diff_text=diff)

        assert violations, "reading the workspace is still an expansion worth review"
        assert [item.id for item in violations] == [
            "HOST-PERMISSION-ALLOW-EXPANDED"
        ] * len(violations)

    def test_the_gate_and_the_table_cannot_disagree(self) -> None:
        """One lattice, two readers: whatever the gate blocks is what the
        table rates above `low`, for every rule in the fixture table."""

        from agents_shipgate.core.host_boundary import (
            _allow_rule_id,
            _is_wildcard_allow,
        )

        rules = sorted({rule for pair in PAIRS for rule in (*pair.before, *pair.after)})
        for rule in [*rules, *WELL_CONFIGURED, "Bash(*)", "*", "WebFetch(*)"]:
            blocks = _allow_rule_id(rule) == "HOST-PERMISSION-WILDCARD-ALLOW"
            table_risk = (
                whole_tool_risk(rule) if _is_wildcard_allow(rule) else scoped_risk(rule)
            )[1]
            assert blocks == (_is_wildcard_allow(rule) and table_risk != "low"), (
                f"{rule}: gate blocks={blocks}, table risk={table_risk}"
            )


class TestPolicyRationale:
    """#657 asked for a defensible severity, which means a written one."""

    def test_every_policy_rule_records_why_its_severity_is_what_it_is(self) -> None:
        import yaml

        text = Path("policies/host-boundary.shipgate.yaml").read_text(encoding="utf-8")
        rules = yaml.safe_load(text)["rules"]

        # The loader ignores unknown keys, so a `why:` field would be
        # silently dropped and look load-bearing. The rationale is a
        # comment, and this reads it as one: each rule's block must carry
        # a `# why:` line between its id and the next rule's.
        blocks = text.split("  - id: ")[1:]
        assert len(blocks) == len(rules)
        for block in blocks:
            rule_id = block.splitlines()[0].strip()
            assert "# why:" in block, f"{rule_id} has no recorded rationale"

    def test_the_policy_never_sits_below_the_safety_floor(self) -> None:
        """An edit here that lowers a severity is refused and replaced, so
        a file that looks lenient would be quietly ignored."""

        import yaml

        from agents_shipgate.core.host_boundary import _RISK_RANK, DEFAULT_RULES

        rules = yaml.safe_load(
            Path("policies/host-boundary.shipgate.yaml").read_text(encoding="utf-8")
        )["rules"]

        assert {rule["id"] for rule in rules} == set(DEFAULT_RULES)
        for rule in rules:
            floor = DEFAULT_RULES[rule["id"]]
            assert _RISK_RANK[rule["risk_level"]] >= _RISK_RANK[floor.risk_level], (
                f"{rule['id']} is below the floor and would be ignored"
            )


class TestSeverity:
    """A severity column that rates reading files `critical` is one a
    reviewer stops reading. These pin the ordering, not the words."""

    @pytest.mark.parametrize(
        ("rule", "expected"),
        [
            ("*", "critical"),
            ("Bash(*)", "critical"),
            ("Shell", "critical"),
            ("WebFetch(*)", "high"),
            ("Write(*)", "high"),
            ("Edit", "high"),
            ("Read(**)", "low"),
            ("Grep", "low"),
            ("mcp__github__*", "high"),
        ],
    )
    def test_whole_tool_risk_follows_what_the_grant_reaches(
        self, rule: str, expected: str
    ) -> None:
        assert whole_tool_risk(rule)[1] == expected

    def test_an_unrecognised_whole_tool_grant_is_not_dismissed(self) -> None:
        """Nothing establishes `low` for a tool we do not know."""

        assert whole_tool_risk("mcp__unknown__*") == ("execute", "high")

    @pytest.mark.parametrize(
        ("rule", "expected"),
        [
            ("Bash(pytest *)", "medium"),
            ("WebFetch(domain:example.com)", "medium"),
            ("Read(src/**)", "low"),
        ],
    )
    def test_scoping_a_rule_lowers_it_but_keeps_the_tool_class(
        self, rule: str, expected: str
    ) -> None:
        assert scoped_risk(rule)[1] == expected

    def test_reading_never_outranks_executing(self) -> None:
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}

        assert order[whole_tool_risk("Read(**)")[1]] < order[scoped_risk("Bash(npm *)")[1]]
        assert order[scoped_risk("Bash(npm *)")[1]] < order[whole_tool_risk("Bash(*)")[1]]


class TestLatticeSoundness:
    """`subsumes` may answer `None`; it may not answer wrongly."""

    def test_a_rule_does_not_widen_itself(self) -> None:
        for pair in PAIRS:
            for rule in pair.before:
                assert subsumes(rule, rule) is False

    def test_the_relation_is_antisymmetric_where_it_decides(self) -> None:
        """Nothing may be reported wider than something wider than it."""

        rules = sorted({rule for pair in PAIRS for rule in (*pair.before, *pair.after)})
        for wider in rules:
            for narrower in rules:
                if subsumes(wider, narrower) is True:
                    assert subsumes(narrower, wider) is not True, (
                        f"{wider} and {narrower} each reported wider than the other"
                    )

    def test_an_undecidable_pair_says_so_rather_than_guessing(self) -> None:
        assert subsumes("Bash(*.py)", "Bash(test_*.py)") is None
        assert subsumes("Bash(a[bc]d)", "Bash(abd)") is None

    def test_unrelated_tools_are_not_wider_than_each_other(self) -> None:
        assert subsumes("Read(**)", "Bash(*)") is False
        assert subsumes("mcp__gitlab__*", "mcp__github__get_issue") is False
