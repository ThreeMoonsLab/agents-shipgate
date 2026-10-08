"""#968: a changed OpenShell policy row names the declarations that changed.

The fixture reproduces the shape of openshift-kni/ai-sandbox#29 as static
policy text: a read-only filesystem path leaves the list, binary selectors
change (two of them wildcards), a rule is removed and release destinations are
added. Nothing is installed or run.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from test_openshell_inputs import REGISTRATION, comparison, selection
from test_partial_host_comparison import _repository
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.capability_diff_rows import capability_diff_rows, review_changes
from agents_shipgate.core.host_grants import diff_host_grants, host_grant_expansion_signals
from agents_shipgate.core.openshell import parse_policy
from agents_shipgate.core.openshell_compare import compare_openshell_grants

POLICY_PATH = "rds-policy/evals/openshell-policy.yaml"

BASE = """\
# comments are never published
version: 1
filesystem_policy:
  include_workdir: false
  read_only:
    - /usr
    - /lib
    - /sbin
    - /etc
    - /proc
    - /sandbox/.uv/python
  read_write:
    - /tmp
    - /dev
landlock:
  compatibility: best_effort
network_policies:
  vertex:
    name: vertex-ai
    endpoints:
      - host: aiplatform.googleapis.com
        port: 443
        enforcement: enforce
    binaries:
      - path: /usr/local/bin/claude
      - path: /usr/bin/node
  google_auth:
    name: google-adc-token
    endpoints:
      - host: oauth2.googleapis.com
        port: 443
        enforcement: enforce
    binaries:
      - path: /usr/local/bin/claude
  npm:
    name: npm-registry
    endpoints:
      - host: registry.npmjs.org
        port: 443
        enforcement: enforce
    binaries:
      - path: /usr/bin/node
  pypi:
    name: python-packages
    endpoints:
      - host: pypi.org
        port: 443
        enforcement: enforce
      - host: files.pythonhosted.org
        port: 443
        enforcement: enforce
    binaries:
      - path: /usr/local/bin/uv
  github:
    name: eval-repo
    endpoints:
      - host: github.com
        port: 443
        enforcement: enforce
    binaries:
      - path: /usr/bin/git
      - path: /usr/lib/git-core/**
"""

HEAD = """\
version: 1
filesystem_policy:
  include_workdir: false
  read_only:
    - /usr
    - /lib
    - /sbin
    - /etc
    - /proc
  read_write:
    - /tmp
    - /dev
landlock:
  compatibility: best_effort
network_policies:
  vertex:
    name: vertex-ai
    endpoints:
      - host: aiplatform.googleapis.com
        port: 443
        enforcement: enforce
    binaries:
      - path: /tmp/**/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude
      - path: /usr/bin/node
  npm:
    name: npm-registry
    endpoints:
      - host: registry.npmjs.org
        port: 443
        enforcement: enforce
    binaries:
      - path: /usr/bin/node
  pypi:
    name: python-packages
    endpoints:
      - host: pypi.org
        port: 443
        enforcement: enforce
      - host: files.pythonhosted.org
        port: 443
        enforcement: enforce
    binaries:
      - path: /usr/bin/python3*
      - path: /tmp/.local/bin/uv
  github:
    name: github
    endpoints:
      - host: github.com
        port: 443
        enforcement: enforce
      - host: release-assets.githubusercontent.com
        port: 443
        enforcement: enforce
      - host: objects.githubusercontent.com
        port: 443
        enforcement: enforce
    binaries:
      - path: /usr/bin/git
      - path: /usr/lib/git-core/**
      - path: /tmp/.local/bin/uv
"""

#: A two-endpoint REST policy whose declarations the controls edit.
REST = """\
version: 1
filesystem_policy: {include_workdir: false, read_only: [/data]}
network_policies:
  api:
    binaries: [{path: /usr/bin/client}]
    endpoints:
      - host: api.example.com
        port: 443
        protocol: rest
        enforcement: enforce
        rules:
          - allow: {method: GET, path: /records}
"""


def _policy_rows(root, *, head="HEAD"):
    result = comparison(root, head=head)
    assert result.comparison_status == "comparable"
    return [row for row in result.rows if row.subject.startswith("openshell")], result


def _repo(tmp_path, base, head, path=POLICY_PATH):
    return _repository(
        tmp_path, {REGISTRATION: selection(path), path: base}, {path: head}
    )


def _diff(root, *args):
    result = CliRunner().invoke(app, ["diff", "--workspace", str(root), "--base", "main", *args])
    assert result.exit_code == 0, result.output
    return result.output


def _grant(text, *, role="authored", defaulted=()):
    policy = parse_policy(text)
    facts = {
        "registration": REGISTRATION, "role": role, "runtime_version": "0.1.2",
        "policy_schema_version": 1, "policy": policy.model_dump(mode="json"),
        "defaulted_fields": list(defaulted), "field_paths": [],
    }
    return {
        "grant_id": "one", "kind": "openshell_policy", "host": "openshell", "source": POLICY_PATH,
        "scope": "repository", "risk": "unknown", "access": "unknown",
        "config_sha256": hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest(),
        "facts": facts,
    }


def _rows(before, after):
    change = {"grant_id": "one", "baseline": before, "current": after}
    signals = host_grant_expansion_signals([change])
    rows = capability_diff_rows({"changes": [change], "expansion_signals": signals})
    return rows, review_changes(rows)


def _change(old, new):
    rows, changes = _rows(_grant(old), _grant(new))
    (row,), (item,) = rows, changes
    return row, item


def test_issue_shape_names_the_changed_paths_binaries_and_destinations(tmp_path):
    root = _repo(tmp_path, BASE, HEAD)
    (row,), result = _policy_rows(root)
    review, = result.review.changes
    detail = review.change
    assert detail is not None
    # The filesystem path that narrowed, by list.
    assert "filesystem read_only -/sandbox/.uv/python" in detail
    # The rule that left, with the binary that reached it.
    assert "removed destination oauth2.googleapis.com:443 (l4) via /usr/local/bin/claude" in detail
    # Binary selectors that changed on a destination that stayed.
    assert "destination aiplatform.googleapis.com:443 (l4) binaries " in detail
    assert "+/tmp/**/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude" in detail
    assert "-/usr/local/bin/claude" in detail
    assert "binaries +/tmp/.local/bin/uv +/usr/bin/python3* -/usr/local/bin/uv" in detail
    # Added destinations, each with its binary selectors.
    assert (
        "added destinations objects.githubusercontent.com:443, release-assets.githubusercontent.com:443 "
        "(l4) via /tmp/.local/bin/uv, /usr/bin/git, /usr/lib/git-core/**" in detail
    )
    # Counts and digests are what the cells still hold; the detail replaces them in the text.
    assert "facts" not in detail
    assert row.before.endswith(row.before.rpartition("facts ")[2]) and "6 endpoint(s)" in row.before
    assert "7 endpoint(s)" in row.after
    assert review.before == row.before and review.after == row.after


def test_issue_shape_keeps_direction_expansion_and_limits(tmp_path):
    (row,), result = _policy_rows(_repo(tmp_path, BASE, HEAD))
    assert row.direction == "changed" and not row.expands and row.severity == "unknown"
    assert "wildcard or unresolved executable selector is outside exact comparison" in row.why
    assert "a single authority direction is unknown" in row.why
    assert "runtime enforcement and freshness are unverified" in row.why
    assert result.review.summary.widenings == 0


def test_diff_text_and_json_say_the_same_thing(tmp_path):
    root = _repo(tmp_path, BASE, HEAD)
    text = _diff(root)
    payload = json.loads(_diff(root, "--json"))
    detail = payload["review"]["changes"][0]["change"]
    assert detail.startswith("authored, OpenShell 0.1.2: filesystem read_only -/sandbox/.uv/python")
    assert detail in text
    assert "a single authority direction is unknown" in text
    # The published rows carry the values they always did.
    row, = [row for row in payload["rows"] if row["subject"].startswith("openshell")]
    assert row["before"].startswith("authored, OpenShell 0.1.2, 6 endpoint(s), facts ")
    assert row["after"].startswith("authored, OpenShell 0.1.2, 7 endpoint(s), facts ")


def test_unchanged_policy_has_no_row(tmp_path):
    root = _repo(tmp_path, BASE, BASE + "\n# a comment only\n")
    rows, result = _policy_rows(root)
    assert rows == [] and result.review.changes == []


def _document(*, read_only=("/data",), rules=None):
    """A policy as JSON (a YAML subset) from ``{rule: (binaries, endpoints)}``."""

    return json.dumps({
        "version": 1,
        "filesystem_policy": {"include_workdir": False, "read_only": list(read_only)},
        "network_policies": {
            name: {"binaries": [{"path": path} for path in binaries], "endpoints": endpoints}
            for name, (binaries, endpoints) in (rules or {}).items()
        },
    })


def _rest(**changes):
    endpoint = {
        "host": "api.example.com", "port": 443, "protocol": "rest", "enforcement": "enforce",
        "rules": [{"allow": {"method": "GET", "path": "/records"}}],
    }
    return {**endpoint, **changes}


def _only(endpoint, binaries=("/usr/bin/client",)):
    return _document(rules={"api": (binaries, [endpoint])})


def test_equivalent_respelling_has_no_row():
    old = _only(_rest())
    new = _document(rules={"renamed": (("/usr/bin/client",), [_rest()])})
    assert compare_openshell_grants(_grant(old), _grant(new)).direction == "equivalent"
    assert diff_host_grants({"grants": [_grant(old)]}, {"grants": [_grant(new)]}) == []


def test_only_an_unnamed_option_value_changed_keeps_the_limit_wording():
    old = _only(_rest(protocol="", rules=[], enforcement="audit", credential_binding={"provider": "one"}))
    new = _only(_rest(protocol="", rules=[], enforcement="audit", credential_binding={"provider": "two"}))
    row, item = _change(old, new)
    assert item.change == (
        "authored, OpenShell 0.1.2: destination api.example.com:443 "
        "(l4, options credential_binding) other options changed (not shown)"
    )
    assert "transport or credential semantics are outside exact comparison" in row.why
    assert "one" not in item.change and "two" not in item.change


def test_a_change_confined_to_composition_names_no_field_and_keeps_the_limit():
    old, new = _grant(_only(_rest())), _grant(_only(_rest()))
    old["facts"]["composition"], new["facts"]["composition"] = {"workspace": "one"}, {"workspace": "two"}
    rows, changes = _rows(old, new)
    assert changes[0].change == (
        "authored, OpenShell 0.1.2: no filesystem, Landlock, process, destination or request "
        "declaration differs in the fields this output names"
    )
    assert "composition workspace resolution changed" in rows[0].why
    assert "a single authority direction is unknown" in rows[0].why


def test_role_change_is_named_beside_the_existing_limit():
    rows, changes = _rows(_grant(_only(_rest())), _grant(_only(_rest()), role="effective_snapshot"))
    assert changes[0].change.startswith("effective_snapshot, OpenShell 0.1.2: role authored → effective_snapshot")
    assert "policy version or input role changed" in rows[0].why


def test_many_filesystem_paths_are_bounded_with_a_count_of_the_rest():
    old = _document(read_only=["/old", *(f"/kept/{n}" for n in range(3))])
    new = _document(read_only=[*(f"/kept/{n}" for n in range(3)), *(f"/new/{n:02}" for n in range(9))])
    _, item = _change(old, new)
    assert item.change == (
        "authored, OpenShell 0.1.2: filesystem read_only "
        "+/new/00 +/new/01 +/new/02 +/new/03 +/new/04 and 4 more -/old"
    )


def test_many_destinations_are_bounded_with_a_count_of_the_rest():
    def rules(count):
        return {f"r{n}": ((f"/bin/{n}",), [{"host": f"h{n:02}.example.com", "port": 443}]) for n in range(count)}

    _, item = _change(_document(rules=rules(1)), _document(rules=rules(13)))
    entries = item.change.partition(": ")[2].split("; ")
    assert len(entries) == 9 and entries[-1] == "and 4 more"
    assert entries[0] == "added destination h01.example.com:443 (l4) via /bin/1"
    # Destinations that changed alike are one entry, itself bounded.
    same = {f"r{n}": (("/bin/a",), [{"host": f"h{n:02}.example.com", "port": 443}]) for n in range(12)}
    _, item = _change(_document(), _document(rules=same))
    assert item.change.endswith(
        "added destinations h00.example.com:443, h01.example.com:443, h02.example.com:443, "
        "h03.example.com:443, h04.example.com:443, and 7 more (l4) via /bin/a"
    )


def test_many_binary_selectors_are_bounded_with_a_count_of_the_rest():
    endpoints = [{"host": "x.example.com", "port": 443}]
    old = _document(rules={"one": (("/a",), endpoints)})
    new = _document(rules={"one": (tuple(f"/bin/{n}" for n in range(8)), endpoints)})
    _, item = _change(old, new)
    assert item.change.endswith(
        "destination x.example.com:443 (l4) binaries +/bin/0 +/bin/1 +/bin/2 +/bin/3 +/bin/4 and 3 more -/a"
    )
    # Added destinations name at most three selectors.
    _, item = _change(_document(), _document(rules={"one": (tuple(f"/bin/{n}" for n in range(8)), endpoints)}))
    assert item.change.endswith("via /bin/0, /bin/1, /bin/2, and 5 more")


def test_request_declarations_are_named_without_query_or_credential_values():
    rules = [
        {"allow": {"method": "GET", "path": "/records"}},
        {"allow": {"method": "POST", "path": "/records", "query": {"token": "abc", "page": "1"}}},
    ]
    new = _only(_rest(rules=rules, deny_rules=[{"method": "DELETE", "path": "/records"}]))
    _, item = _change(_only(_rest()), new)
    assert item.change == (
        "authored, OpenShell 0.1.2: destination api.example.com:443 "
        "(rest, enforce, allow GET /records) → "
        "(rest, enforce, allow GET /records, POST /records[query], deny DELETE /records)"
    )
    assert "abc" not in item.change and "token" not in item.change


def test_a_preset_and_enforcement_change_reads_as_a_declaration_transition():
    old = _only(_rest(rules=[], access="read-only"))
    new = _only(_rest(rules=[], access="read-write", enforcement="audit"))
    row, item = _change(old, new)
    assert item.change.endswith(
        "destination api.example.com:443 (rest, enforce, read-only) → (rest, audit, read-write)"
    )
    assert row.direction == "widened" and row.expands


def test_secret_shaped_values_are_withheld():
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    clean = _grant(_only(_rest()))
    # The reader refuses a document whose values would change under redaction
    # (see below), so a grant like these reaches the rows only from an older
    # saved baseline. The row still withholds the value.
    for edit in (
        {"host": f"{secret}.example.com"},
        {"rules": [{"allow": {"method": "GET", "path": f"/records/{secret}"}}]},
    ):
        _, item = _change_grants(clean, _grant(_only(_rest(**edit))))
        assert secret not in item.change
        assert "<redacted>" in item.change


def test_a_secret_shaped_destination_is_refused_by_the_reader_not_summarized(tmp_path):
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    leaked = _only(_rest(host=f"{secret}.example.com"))
    root = _repo(tmp_path, _only(_rest()), leaked)
    result = comparison(root, head="HEAD")
    assert secret not in result.model_dump_json()
    assert not [row for row in result.rows if row.subject.startswith("openshell")]
    assert "cannot be compared after redaction" in result.model_dump_json()


def _change_grants(old, new):
    rows, changes = _rows(old, new)
    (row,), (item,) = rows, changes
    return row, item


def test_a_newly_selected_document_shows_declarations_and_default_provenance():
    added = _only(_rest(enforcement="audit"))
    grant = _grant(
        added,
        defaulted=["/landlock", "/process", "/network_policies/api/endpoints/0/enforcement"],
    )
    rows, changes = _rows(None, grant)
    (row,), (item,) = rows, changes
    assert row.before == "—" and item.change is None and item.before == "—"
    assert item.after.startswith(row.after + "; declared: ")
    declared = item.after.partition("declared: ")[2]
    assert declared == (
        "filesystem read_only /data, include_workdir false; "
        "landlock omitted (best_effort); process omitted (driver default); "
        "destination api.example.com:443 (rest, audit, allow GET /records) via /usr/bin/client; "
        "1 inspected endpoint(s) default to audit enforcement"
    )
    # Declarations are not a proof of expansion.
    assert not row.expands and row.direction == "changed"
    # The same for a removed document.
    rows, changes = _rows(grant, None)
    assert changes[0].before == f"{rows[0].before}; declared: {declared}"
    assert changes[0].after == "—"


def test_a_newly_selected_document_in_a_repository(tmp_path):
    root = _repository(tmp_path, {}, {REGISTRATION: selection(POLICY_PATH), POLICY_PATH: HEAD})
    row, = [row for row in comparison(root, head="HEAD").review.changes if "openshell" in row.subject]
    declared = row.after.partition("; declared: ")[2]
    assert declared.startswith("filesystem read_only /usr, /lib, /sbin, /etc, /proc, read_write /tmp, /dev, include_workdir false")
    assert "destination " in declared and "default to audit" not in declared
    assert row.expands is False


@pytest.mark.parametrize("surface", ["verify", "preview"])
def test_verify_host_comparison_carries_the_same_detail(tmp_path, surface):
    root = _repo(tmp_path, BASE, HEAD)
    args = ["verify", "--workspace", str(root), "--base", "main", "--head", "HEAD", "--json"]
    if surface == "preview":
        args.insert(1, "--preview")
    result = CliRunner().invoke(app, args)
    assert result.exit_code in (0, 10, 20), result.output
    review = json.loads(result.output)["host_comparison"]["review"]
    assert "filesystem read_only -/sandbox/.uv/python" in review["changes"][0]["change"]


def test_the_pr_comment_and_a_tight_comment_name_the_changed_declarations(tmp_path):
    from agents_shipgate.report.host_comparison import host_comparison_lines

    result = comparison(_repo(tmp_path, BASE, HEAD), head="HEAD")
    whole = "\n".join(host_comparison_lines(result, markdown=True))
    assert "filesystem read_only -/sandbox/.uv/python" in whole
    assert "removed destination oauth2.googleapis.com:443" in whole
    # A bounded surface cuts the entry rather than falling back to counts and digests.
    tight = "\n".join(host_comparison_lines(result, markdown=True, entry_max_chars=0))
    assert "filesystem read_only -/sandbox/.uv/python" in tight
    assert "facts " not in tight and "…" in tight
