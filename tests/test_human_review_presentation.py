from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from agents_shipgate.report.human_review import human_review_lines
from agents_shipgate.report.pr_comment import render_pr_comment
from agents_shipgate.schemas.human_authorization import (
    HumanAuthorizationReviewItemV1,
    review_set_id,
)
from agents_shipgate.schemas.human_review_request import (
    HumanReviewQuestionV1,
    HumanReviewRequestV1,
    build_human_review_request,
)
from scripts.github_check_run import build_check_run_payload
from tests.test_authorization_verify_integration import _committed_review_repo, _git, _verify


@pytest.fixture(scope="module")
def presentation_case(tmp_path_factory):
    repo = _committed_review_repo(tmp_path_factory.mktemp("review-presentation"))
    _git(repo, "remote", "set-url", "origin", "https://github.com/acme/review-agent.git")
    verifier, report, _ = _verify(repo)
    out = repo / "agents-shipgate-reports"
    request = HumanReviewRequestV1.model_validate_json((out / "human-review-request.json").read_text())
    return repo, out, verifier, report, request


def test_real_verify_publishes_the_question_in_the_existing_pr_comment(presentation_case):
    _, out, verifier, report, request = presentation_case
    text = (out / "pr-comment.md").read_text()
    assert "### Review question" in text
    assert "docs.lookup" in text
    assert f"https://github.com/acme/review-agent/blob/{request.source_head_commit_sha}/tools.json" in text
    assert "Accept" in text and "Reject" in text and "Dispute" in text
    assert "No authenticated decision is recorded" in text
    assert "author and bots cannot supply" in text
    assert "runtime behavior" in text
    assert report.release_decision.decision == "review_required"
    assert not verifier.control.permissions.merge


@pytest.mark.parametrize("style", ["capability-review", "findings"])
def test_both_comment_styles_and_the_check_summary_carry_the_same_question(presentation_case, style):
    _, _, verifier, report, request = presentation_case
    before = report.model_dump_json(), verifier.model_dump_json(), request.model_dump_json()
    text = render_pr_comment(verifier, report=report, style=style, human_review_request=request)
    assert text.index("### Review question") < text.index("Release gate:")
    assert len(text) <= 6000
    assert before == (report.model_dump_json(), verifier.model_dump_json(), request.model_dump_json())
    payload = build_check_run_payload(
        verifier=verifier.model_dump(mode="json"), report=report.model_dump(mode="json"),
        summary_markdown=text, check_run_policy="require-mergeable",
    )
    assert payload["conclusion"] == "failure"
    assert "### Review question" in payload["output"]["summary"]
    assert "No authenticated decision is recorded" in payload["output"]["summary"]


def test_no_request_and_mismatched_request_do_not_create_an_approval_route(presentation_case):
    _, _, verifier, report, request = presentation_case
    assert "### Review question" not in render_pr_comment(verifier, report=report)
    stale = request.model_copy(update={"verification_request_id": "sha256:" + "f" * 64})
    text = render_pr_comment(verifier, report=report, human_review_request=stale)
    assert "### Review question" not in text
    assert "human_review_required" in text


def _request_with_rows(request, *, count=1, path="tools.json", question=None):
    fields = {name: getattr(request, name) for name in type(request).model_fields
              if name not in {"schema_version", "review_request_id"}}
    rows = [HumanAuthorizationReviewItemV1(
        review_item_id=f"review-{index:02}", check_id="SHIP-DOC-MISSING-DESCRIPTION", paths=[path]
    ) for index in range(count)]
    fields.update(review_items=rows, review_set_id=review_set_id(rows), questions=[
        HumanReviewQuestionV1(review_item_id=row.review_item_id,
                             question=question or f"Does docs.lookup need its description repaired? {index}")
        for index, row in enumerate(rows)
    ])
    return build_human_review_request(**fields)


@pytest.mark.parametrize("style", ["capability-review", "findings"])
def test_large_scope_keeps_actor_consequences_and_an_exact_omission_count(presentation_case, style):
    _, _, verifier, report, request = presentation_case
    large = _request_with_rows(request, count=40)
    text = render_pr_comment(verifier, report=report, style=style, human_review_request=large)
    assert len(text) <= 6000
    assert "Showing 3 of 40" in text and "37 omitted; do not decide a partial list" in text
    assert "No authenticated decision is recorded" in text
    assert "None grants merge/completion" in text
    if style == "capability-review":
        block = text.split("### Agent instruction block\n```json\n", 1)[1].split("```", 1)[0]
        assert isinstance(json.loads(block), dict)


def test_source_links_quote_the_actual_path_and_escape_repository_text(presentation_case):
    request = _request_with_rows(
        presentation_case[4], path="tools/a [draft] #1?.json",
        question="Accept <script>alert(1)</script> [fake](javascript:evil) for docs.lookup?",
    )
    text = "\n".join(human_review_lines(request))
    assert "/tools/a%20%5Bdraft%5D%20%231%3F.json)" in text
    assert "<script>" not in text and "[fake](javascript:evil)" not in text
    local = request.model_copy(update={"repository_id": "local:workspace"})
    local_text = "\n".join(human_review_lines(local))
    assert "https://local" not in local_text
    assert "tools/a" in local_text


@pytest.mark.parametrize("field", ["decision_id", "input_set_id", "head_tree_sha"])
def test_mismatched_current_evidence_omits_the_question(presentation_case, field):
    _, _, verifier, report, request = presentation_case
    wrong = request.model_copy(update={field: "f" * 40 if field.endswith("sha") else "sha256:" + "f" * 64})
    assert "### Review question" not in render_pr_comment(verifier, report=report, human_review_request=wrong)


@pytest.mark.parametrize("decision", ["blocked", "insufficient_evidence", "passed"])
def test_excluded_current_decisions_keep_their_existing_route(presentation_case, decision):
    _, _, verifier, report, request = presentation_case
    current = verifier.model_copy(update={"decision": decision})
    text = render_pr_comment(current, report=report, human_review_request=request)
    assert "### Review question" not in text
    assert current.control == verifier.control


def test_abbreviated_questions_and_oversized_source_paths_are_explicit(presentation_case):
    request = _request_with_rows(presentation_case[4], question="docs.lookup " + "x" * 600)
    text = "\n".join(human_review_lines(request))
    assert "Some text is abbreviated; read the full request before deciding" in text
    huge = _request_with_rows(presentation_case[4], path="a/" * 600 + "tools.json")
    text = "\n".join(human_review_lines(huge))
    assert "Showing 0 of 1" in text and "1 omitted; do not decide a partial list" in text
    assert len(text) < 2200


_NODE_HARNESS = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const events = [];
function fail(stage) {
  if (input.at === stage) {
    const error = new Error(input.message || 'Resource not accessible');
    error.status = input.status;
    error.response = {headers: input.headers || {}};
    throw error;
  }
}
const github = {
  rest: {issues: {
    listComments: async () => {},
    updateComment: async (args) => { fail('update'); events.push(['update', args]); },
    createComment: async (args) => { fail('create'); events.push(['create', args]); },
  }},
  paginate: async () => {
    fail('list');
    return input.prior ? [{id: 123, body: '<!-- agents-shipgate-pr-comment -->old'}] : [];
  },
};
const summary = {
  addHeading(value) { events.push(['heading', value]); return this; },
  addRaw(value) { events.push(['summary', value]); return this; },
  async write() { events.push(['written']); },
};
const core = {warning(value) { events.push(['warning', value]); }, summary};
const context = {repo: {owner: 'acme', repo: 'review-agent'}, issue: {number: 7}};
const moduleFor = (name) => name === 'fs' ? {
  existsSync() {return true;}, readFileSync() {return input.body;},
} : require(name);
const env = {GITHUB_SERVER_URL: 'https://github.com', GITHUB_RUN_ID: '123'};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
(async () => {
  let thrown = null;
  try { await new AsyncFunction('github', 'core', 'context', 'require', 'process', input.script)
    (github, core, context, moduleFor, {env}); }
  catch(error) { thrown = error.status || String(error); }
  process.stdout.write(JSON.stringify({events, thrown}));
})();
"""


def _run_action_publication(**case):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the GitHub Action script; present on CI runners")
    action = yaml.safe_load(Path("action.yml").read_text())
    script = next(step["with"]["script"] for step in action["runs"]["steps"]
                  if step["name"] == "Comment on pull request")
    result = subprocess.run([node, "-e", _NODE_HARNESS], input=json.dumps({
        "script": script, "body": "<!-- agents-shipgate-pr-comment -->\n### Review question\nEvidence: tools.json",
        **case,
    }), text=True, capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


@pytest.mark.parametrize("at,status,prior", [("list", 403, False), ("create", 403, False), ("update", 404, True)])
def test_action_publication_denial_keeps_review_visible_without_claiming_an_approval(at, status, prior):
    result = _run_action_publication(at=at, status=status, prior=prior)
    assert result["thrown"] is None
    assert not any(kind in {"create", "update"} for kind, *_ in result["events"])
    summaries = "\n".join(row[1] for row in result["events"] if row[0] == "summary")
    assert "### Review question" in summaries and "Evidence: tools.json" in summaries
    assert "No authenticated decision is recorded" in summaries
    assert "verifier gate and current control still apply" in summaries
    assert ["written"] in result["events"]


@pytest.mark.parametrize("status,headers,message", [
    (401, {}, "Bad credentials"), (429, {}, "Too many requests"), (503, {}, "Unavailable"),
    (403, {"x-ratelimit-remaining": "0"}, "Forbidden"),
    (403, {"retry-after": "60"}, "Forbidden"), (403, {}, "Secondary rate limit"),
])
def test_action_does_not_hide_authentication_rate_limit_or_server_failures(status, headers, message):
    result = _run_action_publication(at="list", status=status, headers=headers, message=message)
    assert result["thrown"] == status
    assert not result["events"]


@pytest.mark.parametrize("prior,operation", [(False, "create"), (True, "update")])
def test_normal_publication_still_upserts_one_current_comment(prior, operation):
    result = _run_action_publication(prior=prior)
    assert result["thrown"] is None
    assert len(result["events"]) == 1
    kind, args = result["events"][0]
    assert kind == operation and "### Review question" in args["body"]
    assert "Workflow artifacts:" in args["body"]
