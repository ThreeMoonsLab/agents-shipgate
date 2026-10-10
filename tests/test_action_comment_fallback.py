"""Exercise the shipped Action JavaScript with local GitHub/core stubs (#856)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for the Action script")
ROOT = Path(__file__).resolve().parents[1]
COMMENT = "<!-- agents-shipgate-pr-comment -->\n## Agents Shipgate\n\n### Human summary\n\nReview this change.\n"
LIMITATION = (
    "PR comment publication is unavailable with this token or PR context. "
    "Read the review below in the workflow summary. No authenticated decision is recorded; "
    "the verifier gate and current control still apply."
)
HARNESS = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let summary = '';
const calls = [], warnings = [];
const publish = async (kind, value) => {
  calls.push({kind, value});
  if (input.status) {
    const error = new Error('publication failed');
    error.status = input.status;
    error.response = {headers: input.headers || {}};
    throw error;
  }
};
const github = {
  paginate: async () => input.comments || (input.prior ? [{id: 42, body: '<!-- agents-shipgate-pr-comment --> old'}] : []),
  rest: {issues: {
    listComments: () => {},
    createComment: value => publish('create', value),
    updateComment: value => publish('update', value),
  }},
};
const core = {
  warning: value => warnings.push(value),
  summary: {
    addHeading(value, level) { summary += `<h${level}>${value}</h${level}>\n`; return this; },
    addRaw(value, newline = false) { summary += value + (newline ? '\n' : ''); return this; },
    async write() {},
  },
};
const context = {repo: {owner: 'owner', repo: 'repo'}, issue: {number: 7}};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
(async () => {
  let error = null;
  try { await new AsyncFunction('require', 'github', 'core', 'context', input.script)(require, github, core, context); }
  catch (caught) { error = caught.status; }
  process.stdout.write(JSON.stringify({summary, calls, warnings, error}));
})();
"""


def _run(tmp_path: Path, *, status: int = 0, prior: bool = False, headers=None) -> dict:
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    step = next(s for s in action["runs"]["steps"] if s["name"] == "Comment on pull request")
    (tmp_path / "pr-comment.md").write_text(COMMENT)
    result = subprocess.run(
        [NODE, "-e", HARNESS],
        input=json.dumps({"script": step["with"]["script"], "status": status, "prior": prior, "headers": headers}),
        text=True, capture_output=True, check=True,
        env={**os.environ, "OUTPUT_DIR": str(tmp_path), "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "123"},
    )
    return json.loads(result.stdout)


def _published_body() -> str:
    return COMMENT + "\n\nWorkflow artifacts: https://github.com/owner/repo/actions/runs/123"


@pytest.mark.parametrize("status", [403, 404])
@pytest.mark.parametrize("prior", [False, True])
def test_denied_publication_writes_separated_markdown(tmp_path, status, prior):
    result = _run(tmp_path, status=status, prior=prior)
    assert result["error"] is None
    assert result["warnings"] == [LIMITATION]
    assert result["summary"] == (
        "## Agents Shipgate: PR publication unavailable\n\n"
        + LIMITATION + "\n\n" + _published_body() + "\n"
    )


@pytest.mark.parametrize("prior,kind", [(False, "create"), (True, "update")])
def test_successful_comment_publication_does_not_write_a_fallback(tmp_path, prior, kind):
    result = _run(tmp_path, prior=prior)
    assert result["summary"] == "" and result["warnings"] == [] and result["error"] is None
    [call] = result["calls"]
    assert call["kind"] == kind and call["value"]["body"] == _published_body()
    assert call["value"]["owner"] == "owner" and call["value"]["repo"] == "repo"
    assert call["value"]["comment_id" if prior else "issue_number"] == (42 if prior else 7)


@pytest.mark.parametrize("status,headers", [(500, {}), (403, {"x-ratelimit-remaining": "0"}), (403, {"retry-after": "60"})])
def test_other_publication_failures_remain_errors(tmp_path, status, headers):
    result = _run(tmp_path, status=status, headers=headers)
    assert result["error"] == status and result["summary"] == "" and result["warnings"] == []
