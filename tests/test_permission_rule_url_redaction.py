"""#922: a URL in a permission rule is redacted without collapsing the rule.

`Bash(curl -s http://localhost:8000/*)` and `Bash(curl -s
http://localhost:8000/health)` added together published one row whose after
cell was `Bash(curl -s http://localhost:8000/<redacted-path>`: the URL
redaction swallowed the wildcard and the closing `)`, and the two grants, keyed
by that text, became one change. A reviewer could not tell an endpoint from an
endpoint wildcard, and could not count the rules.

The settings reader now publishes each rule through one function: a URL keeps
its scheme and host, its trailing wildcard and the delimiters after it; a path,
query, fragment or userinfo is never published; and a rule whose published text
withholds part of a URL carries a short digest of the rule as compared, as a
redacted host path does (#590), so two rules stay two rows on every route. The
digest reads no credential, so rotating one is still no change.

Each end-to-end case is a two-commit repository read by `diff` (text and
`--json`), `verify` (JSON, text and the PR comment), `check` and
`audit --host --drift` against a baseline saved at the base.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_claude_permission_rule_model import (
    SETTINGS,
    _invoke,
    _marked,
    _repository,
    _routes,
    _settings,
    _shape,
)
from test_host_diff_review_changes import _check, _verify

from agents_shipgate.core.host_grants import public_permission_rule

WILDCARD = "Bash(curl -s http://localhost:8000/*)"
ENDPOINT = "Bash(curl -s http://localhost:8000/health)"
#: The published form of a rule whose URL path is withheld: the marker, its
#: twelve-digit digest, then whatever followed the path.
_TOKEN = r"<redacted-path>~[0-9a-f]{12}"


def _everything(repo: Path, tmp_path: Path) -> tuple[dict, str]:
    """Every route's output for one repository, and all of it as one string."""

    routes = _routes(repo)
    block, comment, verifier = _verify(repo, tmp_path / "verify-out")
    inventory = _invoke(["audit", "--host", "--workspace", str(repo), "--json"])
    check_text = _check(repo)
    published = "\n".join(
        [
            routes["text"],
            *(json.dumps(routes[name]) for name in ("diff", "check", "verify", "drift")),
            *block, *comment, *check_text, json.dumps(verifier), inventory,
        ]
    )
    return {**routes, "verify_text": block, "comment": comment, "verifier": verifier,
            "check_text": check_text}, published


def test_issue_fixture_is_two_rows_with_their_delimiters_on_every_route(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path, {SETTINGS: _settings()}, {SETTINGS: _settings(allow=[WILDCARD, ENDPOINT])}
    )
    routes, published = _everything(repo, tmp_path)

    diff = routes["diff"]
    assert diff["comparison_status"] == "comparable"
    cells = sorted(row["after"] for row in diff["rows"])
    assert len(cells) == 2, diff["rows"]
    # The wildcard is scope, not a private path: published as written.
    assert WILDCARD in cells
    [endpoint] = [cell for cell in cells if cell != WILDCARD]
    assert re.fullmatch(rf"Bash\(curl -s http://localhost:8000/{_TOKEN}\)", endpoint), endpoint
    assert all(cell.endswith(")") for cell in cells)
    assert diff["review"]["summary"] == {"rows": 2, "changes": 2, "widenings": 2}

    # Text, the PR comment and verify read the same two rules.
    assert "2 change(s), 2 widening what the agent may do (⚠)." in routes["text"]
    for lines in (routes["verify_text"], routes["comment"]):
        joined = "\n".join(lines)
        assert f"allow: {WILDCARD}" in joined
        assert f"allow: {endpoint}" in joined
    assert f"allow: {WILDCARD}" in routes["text"] and f"allow: {endpoint}" in routes["text"]
    assert _marked(routes["verify"]["rows"]) == _marked(diff["rows"])
    assert routes["verify"]["review"] == diff["review"]
    assert routes["verifier"]["host_comparison"]["rows"] == routes["verify"]["rows"]

    # `check` redacts every argument, and still counts two rules.
    assert _shape(routes["check"]["rows"]) == [("added", True), ("added", True)]
    assert sum("allow: Bash(<redacted-arguments>)" in line for line in routes["check_text"]) == 2

    # Drift against the base's baseline names both rules, as published.
    assert routes["drift"]["comparison_status"] == "comparable"
    signals = json.dumps(routes["drift"]["expansion_signals"])
    assert WILDCARD in signals and endpoint in signals

    assert "health" not in published


def test_a_single_url_rule_keeps_its_closing_delimiter(tmp_path: Path) -> None:
    repo = _repository(tmp_path, {SETTINGS: _settings()}, {SETTINGS: _settings(allow=[ENDPOINT])})
    routes, published = _everything(repo, tmp_path)
    [row] = routes["diff"]["rows"]
    assert re.fullmatch(rf"Bash\(curl -s http://localhost:8000/{_TOKEN}\)", row["after"])
    assert routes["diff"]["review"]["summary"] == {"rows": 1, "changes": 1, "widenings": 1}
    assert len(routes["check"]["rows"]) == 1
    assert "health" not in published


def test_identical_rules_in_one_list_stay_one_row(tmp_path: Path) -> None:
    """The control: distinct identities are for distinct rules, not repeated ones."""

    repo = _repository(
        tmp_path, {SETTINGS: _settings()}, {SETTINGS: _settings(allow=[ENDPOINT, ENDPOINT])}
    )
    routes = _routes(repo)
    assert len(routes["diff"]["rows"]) == 1
    assert routes["diff"]["review"]["summary"] == {"rows": 1, "changes": 1, "widenings": 1}
    assert len(routes["verify"]["rows"]) == 1
    assert len(routes["check"]["rows"]) == 1


#: Private URL text that no route may publish, each in a rule of its own.
_PRIVATE = {
    "query": "Bash(curl https://api.example.test/v1/items?team=QUERYCANARY&token=TOKENCANARY)",
    "fragment": "Bash(curl https://docs.example.test/guide#FRAGMENTCANARY)",
    "userinfo": "Bash(curl https://USERCANARY:PASSCANARY@git.example.test/repo.git)",
    "path": "WebFetch(https://hooks.example.test/services/PATHCANARY/*)",
}


def test_query_fragment_userinfo_and_path_are_never_published(tmp_path: Path) -> None:
    repo = _repository(
        tmp_path, {SETTINGS: _settings()}, {SETTINGS: _settings(allow=list(_PRIVATE.values()))}
    )
    routes, published = _everything(repo, tmp_path)
    for canary in ("QUERYCANARY", "TOKENCANARY", "FRAGMENTCANARY", "USERCANARY", "PASSCANARY", "PATHCANARY"):
        assert canary not in published, canary
    cells = sorted(row["after"] for row in routes["diff"]["rows"])
    assert len(cells) == len(_PRIVATE)
    assert all(cell.endswith(")") for cell in cells)
    # What is withheld is named, not dropped, and the scope a wildcard grants stays.
    assert any(re.search(rf"/{_TOKEN}\?<redacted-query>\)$", cell) for cell in cells), cells
    assert any(re.search(rf"/{_TOKEN}#<redacted-fragment>\)$", cell) for cell in cells), cells
    assert any(
        re.search(rf"https://<redacted>@git\.example\.test/{_TOKEN}\)$", cell) for cell in cells
    ), cells
    assert any(re.search(rf"/{_TOKEN}/\*\)$", cell) for cell in cells), cells


def test_rotating_a_credential_in_a_url_rule_is_no_change(tmp_path: Path) -> None:
    """The digest reads no credential: userinfo and a secret-named query value rotate quietly."""

    base = [
        "Bash(curl https://ci:FIRSTPASS@git.example.test/repo.git)",
        "Bash(curl https://api.example.test/v1?token=FIRSTTOKEN)",
    ]
    head = [
        "Bash(curl https://ci:SECONDPASS@git.example.test/repo.git)",
        "Bash(curl https://api.example.test/v1?token=SECONDTOKEN)",
    ]
    repo = _repository(tmp_path, {SETTINGS: _settings(allow=base)}, {SETTINGS: _settings(allow=head)})
    routes = _routes(repo)
    assert routes["diff"]["comparison_status"] == "comparable"
    assert routes["diff"]["rows"] == []
    assert routes["drift"]["expansion_signals"] == []


def test_a_respelled_url_rule_is_still_one_respelled_change(tmp_path: Path) -> None:
    """`:*` and ` *` are one documented grant (#918): their digests read that spelling."""

    repo = _repository(
        tmp_path,
        {SETTINGS: _settings(allow=["Bash(curl http://localhost:8000/api:*)"])},
        {SETTINGS: _settings(allow=["Bash(curl http://localhost:8000/api *)"])},
    )
    routes = _routes(repo)
    [change] = routes["diff"]["review"]["changes"]
    assert (change["direction"], change["expands"]) == ("respelled", False)
    assert routes["drift"]["expansion_signals"] == []
    assert routes["check"]["violations"] == []


def test_changing_only_a_url_path_is_a_change(tmp_path: Path) -> None:
    """Before #922 both paths published alike, so retargeting a rule was silent."""

    repo = _repository(
        tmp_path,
        {SETTINGS: _settings(allow=["Bash(curl -s http://localhost:8000/health)"])},
        {SETTINGS: _settings(allow=["Bash(curl -s http://localhost:8000/admin)"])},
    )
    routes, published = _everything(repo, tmp_path)
    rows = routes["diff"]["rows"]
    assert sorted(row["direction"] for row in rows) == ["added", "removed"]
    [gone] = [row["before"] for row in rows if row["direction"] == "removed"]
    [new] = [row["after"] for row in rows if row["direction"] == "added"]
    assert gone != new
    assert "health" not in published and "admin" not in published


@pytest.mark.parametrize(
    ("rule", "published"),
    [
        # Nothing private: published exactly as written.
        ("Bash(curl -s http://localhost:8000/*)", "Bash(curl -s http://localhost:8000/*)"),
        ("Bash(curl http://localhost:8000/)", "Bash(curl http://localhost:8000/)"),
        ("Bash(curl http://localhost:8000)", "Bash(curl http://localhost:8000)"),
        ("WebFetch(https://*.example.test/**)", "WebFetch(https://*.example.test/**)"),
        ("Bash(npm test *)", "Bash(npm test *)"),
        # A path keeps the wildcard it ends with, after its digest.
        ("Bash(curl http://h.test/api/*)", "Bash(curl http://h.test/<redacted-path>~…/*)"),
        ("Bash(curl http://h.test/api*)", "Bash(curl http://h.test/<redacted-path>~…*)"),
        ("Bash(curl http://h.test/api:*)", "Bash(curl http://h.test/<redacted-path>~…:*)"),
        # The rule's own `)` and a shell separator are not URL text; a URL's own pair is.
        ("Bash(curl http://h.test/a; echo ok)", "Bash(curl http://h.test/<redacted-path>~…; echo ok)"),
        ("Bash(curl http://h.test/wiki/A_(b))", "Bash(curl http://h.test/<redacted-path>~…)"),
        # A query or fragment is named, never dropped, and its wildcard kept.
        ("Bash(curl http://h.test/a?x=1)", "Bash(curl http://h.test/<redacted-path>~…?<redacted-query>)"),
        ("Bash(curl http://h.test/a#top)", "Bash(curl http://h.test/<redacted-path>~…#<redacted-fragment>)"),
        ("Bash(curl http://h.test?x=*)", "Bash(curl http://h.test?<redacted-query>~…*)"),
        # A rule matches text: the scheme and host keep their case.
        ("Bash(curl HTTP://Local.Test/)", "Bash(curl HTTP://Local.Test/)"),
        # Userinfo is a credential: withheld, the host still named, no digest.
        ("Bash(curl https://u:p@h.test/)", "Bash(curl https://<redacted>@h.test/)"),
        # A port that is not a number may be anything.
        ("Bash(curl http://h.test:abc/a)", "Bash(curl http://<invalid-host>~…/<redacted-path>)"),
    ],
)
def test_a_rule_is_published_with_its_structure(rule: str, published: str) -> None:
    """``~…`` stands for the twelve-digit digest."""

    assert re.sub(r"~[0-9a-f]{12}", "~…", public_permission_rule(rule)) == published


def test_a_credential_only_redaction_carries_no_digest() -> None:
    """Rotating it is no change, so its published text is its whole identity, as before."""

    first = public_permission_rule("Bash(curl -H 'Authorization: FIRST' https://h.test)")
    second = public_permission_rule("Bash(curl -H 'Authorization: SECOND' https://h.test)")
    assert first == second == "Bash(curl -H 'Authorization: <redacted>' https://h.test)"


def test_the_digest_tells_rules_apart_and_is_stable() -> None:
    health = public_permission_rule(ENDPOINT)
    assert health == public_permission_rule(ENDPOINT)
    assert health != public_permission_rule("Bash(curl -s http://localhost:8000/ready)")
    # Two spellings of one grant share a digest and keep their own spelling.
    colon = public_permission_rule("Bash(curl http://h.test/api:*)")
    space = public_permission_rule("Bash(curl http://h.test/api *)")
    assert colon != space and colon.removesuffix(":*)") == space.removesuffix(" *)")
    # The same rule under userinfo that rotates keeps its digest.
    assert public_permission_rule("Bash(curl https://a:1@h.test/x)") == public_permission_rule(
        "Bash(curl https://a:2@h.test/x)"
    )
