"""#872: a bound tool's row names what the tool reaches, read from its source.

Paired fixtures: in each pair, the two sides differ only in the fact under test,
so the row has to state that fact rather than a signature alone.
"""

import json

import pytest
from test_application_diff import commit, run
from test_application_diff import repo as repo
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

AGENT = '''import os
import requests
from agents import Agent, function_tool


@function_tool
def lookup(query: str) -> str:
    return query

BODY

agent = Agent(name="assistant", tools=[lookup, act])
'''


def _added(repo, body: str, *, extra: dict[str, str] | None = None, established: bool = True):
    base = commit(repo, {"agent.py": AGENT.replace("BODY", "").replace(", act", "")})
    head = commit(repo, {"agent.py": AGENT.replace("BODY", body), **(extra or {})})
    result = run(repo, base, head)
    [row] = [row for row in result["rows"] if row["tool"] == "act"]
    if established:
        assert row["change"] == "added"
    else:
        assert result["comparison_status"] == "partial"
        assert row["change"] == "not_established"
        assert row["candidate_change"] == "added"
        assert any("constructor" in gap["reason"] for gap in result["head"]["coverage_gaps"])
    return row["after"], result, (base, head)


def _line(body: str, needle: str) -> str:
    """``agent.py:N`` of the first line of the head's agent.py containing ``needle``."""
    lines = AGENT.replace("BODY", body).splitlines()
    return f"agent.py:{next(i for i, line in enumerate(lines, 1) if needle in line)}"


def _text(repo, base: str, head: str) -> str:
    result = CliRunner().invoke(
        app, ["diff", "--application", "--workspace", str(repo), "--base", base, "--head", head]
    )
    assert result.exit_code == 0, result.output
    return result.output


GRAPHQL = '''
@function_tool
def act(number: int) -> dict:
    payload = {"query": DOC, "variables": {"n": number}}
    return requests.post("https://api.example.com/graphql", json=payload, timeout=5).json()
'''


@pytest.mark.parametrize(
    ("doc", "operation", "effect"),
    [
        ('"""query($n: Int!) { issue(number: $n) { title } }"""', "query", "read"),
        ('"""mutation($n: Int!) { closeIssue(number: $n) { ok } }"""', "mutation", "write"),
        ('"{ viewer { login } }"', "query", "read"),
        (
            '"""# a comment with mutation {\nquery Q { a(s: \\"}\\") { b } }\n'
            'fragment F on T { c }"""',
            "query",
            "read",
        ),
    ],
)
def test_the_graphql_operation_not_the_transport_decides_the_effect(repo, doc, operation, effect):
    after, _, _ = _added(repo, GRAPHQL.replace("DOC", doc))
    [call] = after["reach"]["calls"]
    assert (call["method"], call["graphql"], call["effect"]) == ("POST", operation, effect)
    assert call["model_supplied"] == [{"param": "number", "into": "field variables"}]
    assert after["effect_evidence"]["status"] == "structural"
    assert after["effect_evidence"]["conservative_effect"] == effect


@pytest.mark.parametrize("document,established", [("os.environ['DOC']", False), ('os.environ.get("DOC")', True)])
def test_a_graphql_document_that_is_not_a_literal_is_named(repo, document, established):
    # Both carry the endpoint evidence; retaining an imported getter as a
    # subscript receiver additionally withholds constructor ownership.
    after, _, _ = _added(repo, GRAPHQL.replace("DOC", document), established=established)
    [call] = after["reach"]["calls"]
    assert call["graphql"] == "unknown" and call["effect"] is None
    assert any("GraphQL document is not a literal" in item["why"] for item in after["reach"]["limits"])
    assert after["reach"]["effect_claims"] == []
    assert after["effect_evidence"]["status"] != "structural"


FIELDS = '''
from vendor import pick_state


@function_tool
def act(number: int, note: str) -> dict:
    body = {"state": STATE, "labels": ["triage"], "body": note}
    return requests.patch(f"https://api.example.com/issues/{number}", json=body).json()
'''


def test_literal_request_fields_are_named(repo):
    after, _, _ = _added(repo, FIELDS.replace("STATE", '"closed"'))
    [call] = after["reach"]["calls"]
    assert call["method"] == "PATCH" and call["effect"] == "write"
    assert {"field": "state", "value": "closed"} in call["fields"]
    assert {"field": "body", "from": ["note"]} in call["fields"]
    assert after["reach"]["limits"] == []


def test_a_computed_request_field_is_a_named_limit(repo):
    body = FIELDS.replace("STATE", "pick_state(number)")
    after, _, _ = _added(repo, body)
    [call] = after["reach"]["calls"]
    assert {"field": "state", "from": ["number"]} in call["fields"]
    assert after["reach"]["limits"] == [
        {"at": _line(body, "pick_state(number)"), "why": "calls vendor.pick_state, which is not read"}
    ]
    # A write that was made is still established; what was not read is named.
    assert after["effect_evidence"]["conservative_effect"] == "write"


def test_a_field_chosen_among_literals_names_them_and_what_decides(repo):
    body = '''
@function_tool
def act(number: int, verdict: str) -> dict:
    event = "COMMENT"
    if "blocking" in verdict:
        event = "REQUEST_CHANGES"
    elif "clean" in verdict:
        event = "APPROVE"
    url = f"https://api.example.com/pulls/{number}/reviews"
    return requests.post(url, json={"event": event}).json()
'''
    after, _, (base, head) = _added(repo, body)
    [call] = after["reach"]["calls"]
    assert call["fields"] == [
        {
            "field": "event",
            "values": ["APPROVE", "COMMENT", "REQUEST_CHANGES"],
            "decided_by": ["verdict"],
        }
    ]
    text = _text(repo, base, head)
    assert "field event ∈ {APPROVE, COMMENT, REQUEST_CHANGES}, decided by verdict" in text
    assert "model-supplied: number → url; verdict → field event" in text


URL = '''
BASE = "https://api.example.com"
OWNER = "octo"


@function_tool
def act(number: int) -> dict:
    return requests.get(f"{BASE}/repos/{PART}/issues").json()
'''


@pytest.mark.parametrize(
    ("part", "url", "supplied"),
    [
        (
            "number",
            "https://api.example.com/repos/{number}/issues",
            [{"param": "number", "into": "url"}],
        ),
        ("OWNER", "https://api.example.com/repos/octo/issues", None),
    ],
)
def test_a_tool_parameter_in_the_url_is_model_supplied_and_a_constant_is_not(
    repo, part, url, supplied
):
    after, _, _ = _added(repo, URL.replace("PART", part))
    [call] = after["reach"]["calls"]
    assert call["url"] == url
    assert call.get("model_supplied") == supplied
    assert after["effect_evidence"]["status"] == "structural"
    assert after["effect_evidence"]["conservative_effect"] == "read"


AUTH = '''
@function_tool
def act(number: int) -> dict:
    headers = {"Authorization": TOKEN, "Accept": "application/json"}
    return requests.get(f"https://api.example.com/issues/{number}", headers=headers).json()
'''
SECRET = "sk_live_abcdefghijklmnopqrst"


def test_an_environment_credential_is_named_by_its_variable(repo):
    after, _, (base, head) = _added(
        repo, AUTH.replace("TOKEN", "f\"Bearer {os.getenv('SERVICE_TOKEN')}\"")
    )
    [call] = after["reach"]["calls"]
    assert call["credential_sources"] == [{"header": "Authorization", "env": ["SERVICE_TOKEN"]}]
    assert "credential: env SERVICE_TOKEN → header Authorization" in _text(repo, base, head)


def test_a_hard_coded_credential_is_named_and_never_printed(repo):
    after, result, (base, head) = _added(repo, AUTH.replace("TOKEN", f'"Bearer {SECRET}"'))
    [call] = after["reach"]["calls"]
    assert call["credential_sources"] == [{"header": "Authorization", "literal": True}]
    assert SECRET not in json.dumps(result)
    text = _text(repo, base, head)
    assert "credential: a literal (not printed) → header Authorization" in text
    assert SECRET not in text


def test_a_secret_literal_in_the_url_is_not_printed(repo):
    body = f'''
@function_tool
def act(number: int) -> dict:
    return requests.get(f"https://api.example.com/issues/{{number}}?api_key={SECRET}").json()
'''
    after, result, _ = _added(repo, body)
    [call] = after["reach"]["calls"]
    assert SECRET not in json.dumps(result)
    assert "api_key=[REDACTED" in call["url"]


def _chain(hops: int) -> str:
    return ('from support import hop1\n\n@function_tool\ndef act(number: int) -> dict:\n'
            '    return hop1(f"https://api.example.com/issues/{number}")\n')


def _chain_source(hops: int) -> str:
    helpers = "\n".join(
        f"def hop{index}(url):\n    return {'hop' + str(index + 1) + '(url)' if index < hops else 'requests.get(url).json()'}\n"
        for index in range(1, hops + 1)
    )
    return "import requests\n\n" + helpers


def _helper_line(hops: int, needle: str) -> str:
    return f"support.py:{next(i for i, line in enumerate(_chain_source(hops).splitlines(), 1) if needle in line)}"


def test_a_read_through_helpers_within_the_bound_is_established(repo):
    body = _chain(3)
    after, _, _ = _added(repo, body, extra={"support.py": _chain_source(3)})
    [call] = after["reach"]["calls"]
    assert call["via"] == [
        f"{_line(body, 'return hop1(')} hop1",
        f"{_helper_line(3, 'return hop2(')} hop2",
        f"{_helper_line(3, 'return hop3(')} hop3",
    ]
    assert after["reach"]["effect_claims"] == [
        {"effect": "read", "at": _line(body, "def act("), "calls": 1}
    ]
    assert after["effect_evidence"]["status"] == "structural"


def test_a_helper_beyond_the_bound_is_a_named_limit_not_a_read(repo):
    body = _chain(4)
    after, _, _ = _added(repo, body, extra={"support.py": _chain_source(4)})
    assert after["reach"]["calls"] == []
    assert after["reach"]["limits"] == [
        {
            "at": _helper_line(4, "return hop4("),
            "why": "calls hop4, more than 3 helper calls from the tool; not read",
        }
    ]
    assert after["reach"]["effect_claims"] == []
    assert after["effect_evidence"]["status"] != "structural"


def test_a_call_the_read_cannot_follow_blocks_a_read_claim(repo):
    body = '''
import storage


@function_tool
def act(number: int) -> dict:
    storage.remember(number)
    return requests.get(f"https://api.example.com/issues/{number}").json()
'''
    after, _, _ = _added(repo, body)
    [call] = after["reach"]["calls"]
    assert call["effect"] == "read"
    assert after["reach"]["effect_claims"] == []
    assert after["reach"]["limits"][0]["why"] == "calls storage.remember, which is not read"


@pytest.mark.parametrize("external_call", [False, True], ids=["saved-client-unread", "direct-external-call"])
def test_a_helper_in_another_module_is_followed_and_located(repo, external_call):
    body = '''
from support import client


@function_tool
def act(number: int) -> dict:
    return client.close_issue(number)
'''
    support = '''import os
import requests

BASE = os.environ["API_BASE"]
session = requests.Session()


def close_issue(number):
    return session.delete(f"{BASE}/issues/{number}", headers={"X-Api-Key": os.environ["API_KEY"]})
'''
    if external_call:
        support = support.replace('os.environ["API_BASE"]', 'os.environ.get("API_BASE")')
        support = support.replace('os.environ["API_KEY"]', 'os.environ.get("API_KEY")')
        support = support.replace("return session.delete", "return requests.delete")
    after, _, (base, head) = _added(
        repo,
        body,
        extra={"support/__init__.py": "", "support/client.py": support},
        established=external_call,
    )
    [call] = after["reach"]["calls"]
    assert call["method"] == "DELETE" and call["effect"] == "destructive"
    assert call["url"] == "{env API_BASE}/issues/{number}"
    assert call["at"] == "support/client.py:9"
    assert call["via"] == [f"{_line(body, 'client.close_issue(')} close_issue"]
    assert call["credential_sources"] == [{"header": "X-Api-Key", "env": ["API_KEY"]}]
    assert after["effect_evidence"]["conservative_effect"] == "destructive"
    assert after["effect_evidence"]["status"] == "structural"
    text = _text(repo, base, head)
    assert "reaches: DELETE {env API_BASE}/issues/{number} at support/client.py:9" in text
    assert "effect: destructive (structural evidence: outbound call at support/client.py:9)" in text


def test_reach_is_evidence_not_meaning(repo):
    # A helper's endpoint changes; the tool's own code does not. No row claims
    # a changed binding the implementation digest does not show.
    body = '''
from support import send


@function_tool
def act(number: int) -> dict:
    return send(number)
'''
    helper = 'import requests\n\n\ndef send(number):\n    return requests.METHOD(f"https://x.test/{number}")\n'
    base = commit(
        repo,
        {
            "agent.py": AGENT.replace("BODY", body),
            "support.py": helper.replace("METHOD", "get"),
        },
    )
    head = commit(repo, {"support.py": helper.replace("METHOD", "delete")})
    result = run(repo, base, head)
    assert result["rows"] == []


def test_two_same_named_adk_agents_are_located_at_their_own_constructions(repo):
    source = (
        "from google.adk.agents import LlmAgent\n\n\n"
        "def details(q: str) -> str:\n    return q\n\n\n"
        "def submit(q: str) -> str:\n    return q\n\n\n"
        "def submit_orchestrated(q: str) -> str:\n    return q\n\n\n"
        'root_agent = LlmAgent(name="reviewer", model="m", tools=[details, submit])\n\n\n'
        "def make_agent():\n"
        '    return LlmAgent(name="reviewer", model="m", tools=[details, submit_orchestrated])\n'
    )
    base = commit(repo, {"README.md": "empty"})
    head = commit(repo, {"agent.py": source})
    result = run(repo, base, head)
    located = {
        row["tool"]: (row["after"]["binding_location"], row["after"].get("construction_sites"))
        for row in result["rows"]
    }
    assert located == {
        "details": ("agent.py:16", ["agent.py:16", "agent.py:20"]),
        "submit": ("agent.py:16", None),
        "submit_orchestrated": ("agent.py:20", None),
    }
    assert any(
        "constructed more than once in agent.py (lines 16, 20)" in limit
        for limit in result["head"]["limits"]
    )
    text = _text(repo, base, head)
    assert "details(q) -> str at agent.py:16 (also listed at agent.py:20)" in text


def test_reach_is_deterministic(repo):
    after, _, (base, head) = _added(repo, _chain(2), extra={"support.py": _chain_source(2)})
    again = run(repo, base, head)
    [row] = [row for row in again["rows"] if row["tool"] == "act"]
    assert row["after"]["reach"] == after["reach"]


def test_construction_sites_are_in_line_order(repo):
    # A factory above the root agent was read first; the row still names the
    # earlier construction first, as the limit does (#872 review).
    source = (
        "from google.adk.agents import LlmAgent\n\n\n"
        "def details(q: str) -> str:\n    return q\n\n\n"
        "def make_agent():\n"
        '    return LlmAgent(name="reviewer", model="m", tools=[details])\n\n\n'
        'root_agent = LlmAgent(name="reviewer", model="m", tools=[details, make_agent])\n'
    )
    base = commit(repo, {"README.md": "empty"})
    head = commit(repo, {"agent.py": source})
    result = run(repo, base, head)
    [row] = [row for row in result["rows"] if row["tool"] == "details"]
    assert row["after"]["construction_sites"] == ["agent.py:9", "agent.py:12"]
    assert row["after"]["binding_location"] == "agent.py:9"


@pytest.mark.parametrize("credential,established", [('os.environ["TOKEN"]', False), ('os.environ.get("TOKEN")', True)])
def test_a_credential_only_some_call_sites_send_says_so(repo, credential, established):
    body = '''
@function_tool
def act(number: int) -> dict:
    url = f"https://api.example.com/issues/{number}"
    response = requests.get(url, headers={"Authorization": os.environ["TOKEN"]})
    if response.status_code == 401:
        response = requests.get(url)
    return response.json()
'''
    body = body.replace('os.environ["TOKEN"]', credential)
    _, _, (base, head) = _added(repo, body, established=established)
    text = _text(repo, base, head)
    assert "credential: env TOKEN → header Authorization (at some call sites)" in text


def test_a_row_names_what_the_tool_reaches_beyond_http(repo):
    # #913: a process, a database and a file, each named with its location;
    # the strongest supports the effect evidence.
    body = '''
import sqlite3
import subprocess


@function_tool
def act(number: int) -> dict:
    subprocess.run(["git", "fetch", "origin", str(number)], check=True)
    with sqlite3.connect("issues.db") as conn:
        conn.execute("UPDATE issues SET seen = 1 WHERE number = ?", (number,))
    with open(f"logs/{number}.txt", "a") as log:
        log.write("seen")
    return {"ok": True}
'''
    after, _, (base, head) = _added(repo, body)
    effects = after["reach"]["effects"]
    assert [(item["family"], item["operation"], item.get("target"), item["at"]) for item in effects] == [
        ("process", "execute", "git", _line(body, "subprocess.run(")),
        ("database", "write", "issues", _line(body, "conn.execute(")),
        ("filesystem", "write", "logs/{number}.txt", _line(body, "with open(")),
    ]
    assert "fetch" not in json.dumps(effects) and "seen = 1" not in json.dumps(effects)
    assert after["effect_evidence"]["conservative_effect"] == "code_execution"
    assert after["effect_evidence"]["status"] == "structural"
    sources = {claim["source"] for claim in after["effect_evidence"]["claims"]}
    assert sources == {"source_library_call"}
    text = _text(repo, base, head)
    assert f"reaches: process execute git (subprocess.run) at {_line(body, 'subprocess.run(')}" in text
    assert "  model-supplied: number → command" in text
    assert (
        "reaches: database write UPDATE on issues (sqlite, sqlite3 connection.execute) "
        f"at {_line(body, 'conn.execute(')}"
    ) in text
    assert f"reaches: filesystem write logs/{{number}}.txt (open) at {_line(body, 'with open(')}" in text
    assert (
        "effect: code_execution (structural evidence: process execute at "
        f"{_line(body, 'subprocess.run(')})"
    ) in text


def test_a_tool_that_only_reads_a_database_it_opens_reads(repo):
    body = '''
import sqlite3


@function_tool
def act(number: int) -> list:
    with sqlite3.connect("issues.db") as conn:
        return conn.execute("SELECT title FROM issues WHERE number = ?", (number,)).fetchall()
'''
    after, _, (base, head) = _added(repo, body)
    assert after["reach"]["effect_claims"] == [
        {"effect": "read", "at": _line(body, "def act("), "calls": 0, "effects": 1}
    ]
    assert after["effect_evidence"]["conservative_effect"] == "read"
    text = _text(repo, base, head)
    assert "effect: read (structural evidence: every call was followed, and everything it reaches reads)" in text
