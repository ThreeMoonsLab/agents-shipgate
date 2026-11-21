"""#1165: the Action's Check Run is concluded only from evidence this run validated.

The payload step used to read whatever JSON sat in the output directory, and
both it and the step that creates the check ran under ``always()`` with no tie
to the outputs step. So a run whose CLI stopped before publishing anything, or
whose directory the outputs step refused, could still post a passing check.

These tests run the Action's own steps from ``action.yml`` — the policy check,
"Run Agents Shipgate", the outputs step, the payload step and the github-script
step that creates the check — in the Action's order and under each step's own
``if:``, against real ``verify`` output on git copies of the samples, with this
tree's CLI standing in for the installed engine. Each test marked as a defect
case fails on the parent branch.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_action_comment_fallback import NODE
from tests.test_host_only_advisory_recipe import _bin_dir, _clean_env, _git

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
STEPS: list[dict[str, Any]] = ACTION["runs"]["steps"]
VALIDATE = "Validate Agents Shipgate check-run policy"
RUN = "Run Agents Shipgate"
ATTEST = "Build Agents Shipgate attestation"
BUNDLE = "Build Agents Shipgate org evidence bundle"
EXTRACT = "Extract Agents Shipgate outputs"
ANNOTATE = "Emit Agents Shipgate annotations"
COMMENT = "Comment on pull request"
PAYLOAD = "Build Agents Shipgate check-run payload"
CREATE = "Create Agents Shipgate check run"
#: The publication sequence, in the order action.yml runs it.
PUBLICATION = (EXTRACT, PAYLOAD, CREATE)
#: Every step that publishes what the output directory holds, in that order.
EVERY_PUBLISHER = (ATTEST, BUNDLE, EXTRACT, ANNOTATE, COMMENT, PAYLOAD, CREATE)
NO_RESULT_NOTE = "This run has no validated verification result, so no review is shown."
POLICIES = ("advisory", "blocked-fails", "require-mergeable")
#: How each policy concludes a run that states no verdict: never `success`.
NO_VERDICT = {"advisory": "neutral", "blocked-fails": "failure", "require-mergeable": "failure"}
PASSED, BLOCKED, REVIEW = "clean_read_only_agent", "support_refund_agent", "hitl_evidence_agent"
#: An input the real CLI refuses before it touches the output directory.
REFUSED_INPUT = {"fail_on": "not-a-severity"}

pytestmark = [
    pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None,
                       reason="The composite steps are bash scripts."),
    pytest.mark.skipif(NODE is None, reason="Node is required for the github-script step."),
]

CHECK_HARNESS = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const calls = [], comments = [], warnings = [], failures = [];
const github = {
  paginate: async () => [],
  rest: {
    checks: {create: async (value) => { calls.push(value); }},
    issues: {
      listComments: () => {},
      createComment: async (value) => { comments.push(value); },
      updateComment: async (value) => { comments.push(value); },
    },
  },
};
const core = {
  warning: (value) => warnings.push(String(value)),
  setFailed: (value) => failures.push(String(value)),
  summary: {addRaw() { return this; }, async write() {}},
};
const context = {
  repo: {owner: 'owner', repo: 'repo'},
  issue: {number: 7},
  sha: input.sha,
  payload: {pull_request: {head: {sha: input.head_sha}}},
};
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
(async () => {
  let error = null;
  try {
    await new AsyncFunction('require', 'github', 'core', 'context', input.script)(
      require, github, core, context);
  } catch (caught) { error = String((caught && caught.message) || caught); }
  process.stdout.write(JSON.stringify({calls, comments, warnings, failures, error}));
})();
"""


# --- the expressions action.yml uses, read the way the runner reads them -----

_TOKEN = re.compile(
    r"\s*(always\(\)|success\(\)|failure\(\)|cancelled\(\)|&&|\|\||==|!=|\(|\)|'[^']*'"
    r"|[A-Za-z_][\w.-]*)"
)
_STATUS = {"always()", "success()", "failure()", "cancelled()"}


def _tokens(expression: str) -> list[str]:
    text = expression.strip()
    if text.startswith("${{"):
        assert text.endswith("}}"), expression
        text = text[3:-2]
    tokens, position = [], 0
    while text[position:].strip():
        match = _TOKEN.match(text, position)
        assert match, f"unsupported expression: {expression!r}"
        tokens.append(match.group(1))
        position = match.end()
    return tokens


def _truthy(value: object) -> bool:
    return value != "" if isinstance(value, str) else bool(value)


@dataclass
class ActionRun:
    """One use of the Action in a job: its inputs, contexts and step results."""

    workspace: Path
    inputs: dict[str, str]
    github: dict[str, Any]
    outputs: dict[str, dict[str, str]]
    outcomes: dict[str, str] = field(default_factory=dict)
    logs: dict[str, str] = field(default_factory=dict)
    check_runs: list[dict[str, Any]] = field(default_factory=list)
    pr_comments: list[dict[str, Any]] = field(default_factory=list)
    script_failures: list[str] = field(default_factory=list)

    @property
    def reports(self) -> Path:
        return self.workspace / self.inputs["output_dir"]

    @property
    def pr_head(self) -> str:
        return self.github["event"]["pull_request"]["head"]["sha"]

    def lookup(self, name: str) -> object:
        scope, *rest = name.split(".")
        if scope == "inputs":
            return self.inputs.get(rest[0], "")
        if scope == "github":
            value: object = self.github
            for part in rest:
                value = value.get(part, "") if isinstance(value, dict) else ""
            return value
        if scope == "steps":
            step_id, kind, *key = rest
            if kind == "outputs":
                return self.outputs.get(step_id, {}).get(key[0], "")
            return self.outcomes.get(step_id, "")
        raise AssertionError(f"unsupported context {name!r}")

    def value(self, expression: str, *, failed: bool = False) -> object:
        tokens = _tokens(expression)
        position = 0

        def peek() -> str | None:
            return tokens[position] if position < len(tokens) else None

        def take() -> str:
            nonlocal position
            position += 1
            return tokens[position - 1]

        def primary() -> object:
            token = take()
            if token == "(":
                inner = disjunction()
                assert take() == ")"
                return inner
            if token in _STATUS:
                return {"always()": True, "success()": not failed,
                        "failure()": failed, "cancelled()": False}[token]
            if token.startswith("'"):
                return token[1:-1]
            return self.lookup(token)

        def comparison() -> object:
            left = primary()
            while peek() in {"==", "!="}:
                operator, right = take(), primary()
                # The runner compares strings ignoring case.
                equal = str(left).lower() == str(right).lower()
                left = equal if operator == "==" else not equal
            return left

        def conjunction() -> object:
            left = comparison()
            while peek() == "&&":
                take()
                right = comparison()
                left = right if _truthy(left) else left
            return left

        def disjunction() -> object:
            left = conjunction()
            while peek() == "||":
                take()
                right = conjunction()
                left = left if _truthy(left) else right
            return left

        result = disjunction()
        assert position == len(tokens), expression
        return result

    def condition(self, step: dict[str, Any]) -> bool:
        expression = step.get("if", "success()")
        failed = "failure" in self.outcomes.values()
        holds = _truthy(self.value(expression, failed=failed))
        # With no status function, the runner reads `success() && (...)`.
        if not _STATUS & set(_tokens(expression)):
            holds = holds and not failed
        return holds

    def expand(self, text: str) -> str:
        return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(self.value(match.group(1))), text)

    def copy_to(self, root: Path) -> ActionRun:
        workspace = root / "repo"
        shutil.copytree(self.workspace, workspace, symlinks=True)
        return ActionRun(
            workspace=workspace, inputs=dict(self.inputs), github=copy.deepcopy(self.github),
            outputs=copy.deepcopy(self.outputs), outcomes=dict(self.outcomes),
            logs=dict(self.logs),
        )


def _step(name: str) -> dict[str, Any]:
    return next(step for step in STEPS if step.get("name") == name)


def _run_step(run: ActionRun, name: str, scratch: Path) -> None:
    """Execute one of action.yml's steps as the runner would, if its `if:` holds."""

    step = _step(name)
    key = step.get("id", name)
    if not run.condition(step):
        run.outcomes[key] = "skipped"
        return
    scratch.mkdir(parents=True, exist_ok=True)
    env = _clean_env()
    for variable in ("CLAUDECODE", "CURSOR_TRACE_ID"):
        env.pop(variable, None)
    env.update({variable: run.expand(str(value)) for variable, value in step.get("env", {}).items()})
    output = scratch / f"github-output-{key}"
    output.write_text("", encoding="utf-8")
    env.update({
        "PYTHONPATH": str(ROOT / "src"),
        "GITHUB_ACTION_PATH": str(ROOT),
        "GITHUB_WORKSPACE": str(run.workspace),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(scratch / "step-summary"),
    })
    if "run" in step:
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", run.expand(step["run"])],
            cwd=run.workspace, env=env, capture_output=True, text=True, timeout=600,
        )
        run.logs[name] = result.stdout + result.stderr
        run.outcomes[key] = "success" if result.returncode == 0 else "failure"
    else:
        assert step["uses"].startswith("actions/github-script@"), step["uses"]
        request = {"script": step["with"]["script"], "sha": run.github["sha"],
                   "head_sha": run.pr_head}
        result = subprocess.run(
            [NODE, "-e", CHECK_HARNESS], input=json.dumps(request), cwd=run.workspace,
            env=env, capture_output=True, text=True, timeout=120, check=True,
        )
        posted = json.loads(result.stdout)
        run.logs[name] = json.dumps(posted)
        run.check_runs.extend(posted["calls"])
        run.pr_comments.extend(posted["comments"])
        run.script_failures.extend(posted["failures"])
        failed = bool(posted["failures"]) or posted["error"] is not None
        run.outcomes[key] = "failure" if failed else "success"
    if "id" in step:
        lines = output.read_text(encoding="utf-8").splitlines()
        run.outputs[step["id"]] = dict(line.split("=", 1) for line in lines if "=" in line)


def _publish(run: ActionRun, scratch: Path, names: tuple[str, ...] = PUBLICATION) -> ActionRun:
    for name in names:
        _run_step(run, name, scratch)
    return run


def _sample_run(root: Path, sample: str, *, ci_mode: str = "advisory") -> ActionRun:
    """A pull request on a git copy of ``sample``, up to and including the run step."""

    workspace = root / "repo"
    shutil.copytree(ROOT / "samples" / sample, workspace)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    base = _git(workspace, "rev-parse", "HEAD").strip()
    (workspace / "NOTES.md").write_text("A change under review.\n", encoding="utf-8")
    _git(workspace, "add", "NOTES.md")
    _git(workspace, "commit", "-qm", "head")
    head = _git(workspace, "rev-parse", "HEAD").strip()
    _git(workspace, "remote", "add", "origin", "https://github.com/owner/repo.git")
    bin_dir = _bin_dir(root)
    inputs = {name: str(spec.get("default", "")) for name, spec in ACTION["inputs"].items()}
    inputs.update({"check_run": "true", "check_run_policy": "advisory", "ci_mode": ci_mode,
                   "base_ref": base})
    run = ActionRun(
        workspace=workspace, inputs=inputs,
        github={"sha": head, "event_name": "pull_request", "base_ref": "main",
                "event": {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}},
        # What the setup-python and install steps hand the later steps, by path.
        outputs={"install": {"python": str(bin_dir / "python"), "bin": str(bin_dir)},
                 "python": {"python-path": str(bin_dir / "python")}},
    )
    return _publish(run, root / "scratch", (VALIDATE, RUN))


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory):
    """Real runs, one per sample and CI mode, each copied before a test touches it."""

    cache: dict[tuple[str, str], ActionRun] = {}

    def make(sample: str, ci_mode: str = "advisory") -> ActionRun:
        if (sample, ci_mode) not in cache:
            root = tmp_path_factory.mktemp(f"{sample}-{ci_mode}")
            cache[(sample, ci_mode)] = _sample_run(root, sample, ci_mode=ci_mode)
        return cache[(sample, ci_mode)]

    return make


def _stub_cli(root: Path, body: str) -> Path:
    """A CLI directory whose `agents-shipgate` runs ``body`` instead of the engine."""

    directory = root / "stub-bin"
    directory.mkdir(exist_ok=True)
    script = directory / "agents-shipgate"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return directory


def _rerun(run: ActionRun, scratch: Path, *, cli: Path | None = None, **inputs: str) -> ActionRun:
    """The Action used again on the same workspace, up to and including its run step.

    A re-run on a runner that keeps its workspace, or a second use of the
    Action in one job, starts with the earlier run's output directory in place
    and a fresh `steps` context.
    """

    run.inputs.update(inputs)
    install = dict(run.outputs["install"])
    if cli is not None:
        install["bin"] = str(cli)
    run.outputs = {"install": install, "python": run.outputs["python"]}
    run.outcomes, run.logs = {}, {}
    run.check_runs.clear()
    run.pr_comments.clear()
    run.script_failures.clear()
    return _publish(run, scratch, (VALIDATE, RUN))


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _posted(run: ActionRun) -> dict[str, Any]:
    assert len(run.check_runs) == 1, run.logs
    return run.check_runs[0]


def _assert_no_verdict(run: ActionRun, policy: str, *, reason: str) -> None:
    check = _posted(run)
    assert check["conclusion"] == NO_VERDICT[policy], (policy, check["output"]["title"])
    assert check["head_sha"] == run.pr_head
    assert check["output"]["annotations"] == []
    assert reason in check["output"]["summary"], check["output"]["summary"]


def _under_each_policy(run: ActionRun, scratch: Path) -> dict[str, dict[str, Any]]:
    """The outputs step once, then the payload and check steps under each policy.

    The policy is read by the validation and payload steps only, and in verify
    mode the validation step admits all three, so the steps before the payload
    step are the same for each.
    """

    _publish(run, scratch, (EXTRACT,))
    posted = {}
    for policy in POLICIES:
        run.inputs["check_run_policy"] = policy
        run.check_runs.clear()
        _publish(run, scratch, (PAYLOAD, CREATE))
        posted[policy] = _posted(run)
    return posted


def _assert_no_verdict_under_each_policy(run: ActionRun, scratch: Path, *, reason: str) -> None:
    for policy, check in _under_each_policy(run, scratch).items():
        run.check_runs[:] = [check]
        _assert_no_verdict(run, policy, reason=reason)


# --- a complete run keeps its verdict and its policy mapping ------------------


@pytest.mark.parametrize(
    ("sample", "ci_mode", "exit_code", "verdict", "conclusions"),
    [
        pytest.param(PASSED, "advisory", "0", "mergeable",
                     dict.fromkeys(POLICIES, "success"), id="passed"),
        pytest.param(BLOCKED, "advisory", "0", "blocked",
                     dict.fromkeys(POLICIES, "failure"), id="blocked"),
        # The configured gate failing (exit 20) is a run that completed: its
        # receipt is admitted and its own verdict published, not a refusal.
        pytest.param(BLOCKED, "strict", "20", "blocked",
                     dict.fromkeys(POLICIES, "failure"), id="blocked-gate-exit"),
        pytest.param(REVIEW, "advisory", "0", "human_review_required",
                     {"advisory": "neutral", "blocked-fails": "neutral",
                      "require-mergeable": "failure"}, id="review-required"),
    ],
)
def test_a_complete_run_publishes_its_own_verdict_under_each_policy(
    completed, tmp_path: Path, sample: str, ci_mode: str, exit_code: str, verdict: str,
    conclusions: dict[str, str],
) -> None:
    run = completed(sample, ci_mode).copy_to(tmp_path)
    assert run.outputs["scan"]["exit_code"] == exit_code, run.logs

    posted = _under_each_policy(run, tmp_path / "scratch")

    assert run.outcomes["report_outputs"] == "success", run.logs
    receipt = _json(run.reports / "verification-receipt.json")
    assert run.outputs["report_outputs"]["receipt_id"] == receipt["receipt_id"]
    assert run.outputs["report_outputs"]["merge_verdict"] == verdict
    assert {policy: check["conclusion"] for policy, check in posted.items()} == conclusions
    # The summary is the run's own PR comment, which its receipt binds.
    comment = (run.reports / "pr-comment.md").read_text(encoding="utf-8").strip()
    for check in posted.values():
        assert (check["name"], check["status"]) == ("Agents Shipgate", "completed")
        assert check["head_sha"] == run.pr_head
        assert check["output"]["title"].startswith(f"merge_verdict: {verdict}")
        assert check["output"]["summary"].startswith(comment[:200])
    assert run.script_failures == []


def test_a_scan_run_states_no_verdict_and_is_neutral(completed, tmp_path: Path) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    _rerun(run, tmp_path / "rerun", verify_mode="scan")
    _publish(run, tmp_path / "scratch")
    assert run.outputs["scan"]["exit_code"] == "0", run.logs
    assert not (run.reports / "verifier.json").exists()
    assert _posted(run)["conclusion"] == "neutral"


# --- defect: an early CLI failure cannot publish an earlier run's verdict ----

EARLY_FAILURES = [
    pytest.param(REFUSED_INPUT, None, "2", id="refused-input"),
    pytest.param({}, "exit 3", "3", id="input-error"),
    pytest.param({}, "exit 4", "4", id="internal-error"),
    pytest.param({}, "kill -9 $$", "137", id="killed"),
]


@pytest.mark.parametrize(("inputs", "stub", "exit_code"), EARLY_FAILURES)
def test_an_early_cli_failure_does_not_publish_an_earlier_runs_passing_verdict(
    completed, tmp_path: Path, inputs: dict[str, str], stub: str | None, exit_code: str,
) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    earlier = _json(run.reports / "verification-receipt.json")
    cli = _stub_cli(tmp_path, stub) if stub is not None else None
    _rerun(run, tmp_path / "rerun", cli=cli, **inputs)
    assert run.outputs["scan"]["exit_code"] == exit_code, run.logs
    # The earlier run's complete and internally valid artifact set is still there.
    assert _json(run.reports / "verification-receipt.json") == earlier

    _assert_no_verdict_under_each_policy(run, tmp_path / "scratch", reason="is not this run")
    assert run.outcomes["report_outputs"] == "failure"
    assert "is not this run's" in run.logs[EXTRACT]
    assert run.outputs["report_outputs"] == {}


def test_a_run_that_fails_after_writing_its_artifacts_authorizes_nothing(
    completed, tmp_path: Path,
) -> None:
    """A CLI that wrote a passing set and then exited 4 did not complete."""

    run = completed(PASSED).copy_to(tmp_path)
    real = Path(run.outputs["install"]["bin"]) / "agents-shipgate"
    cli = _stub_cli(tmp_path, f'"{real}" "$@"\nexit 4')
    _rerun(run, tmp_path / "rerun", cli=cli, check_run_policy="require-mergeable")
    assert run.outputs["scan"]["exit_code"] == "4"
    assert _json(run.reports / "verifier.json")["can_merge_without_human"] is True

    _publish(run, tmp_path / "scratch")

    _assert_no_verdict(run, "require-mergeable", reason="exited 4")
    assert run.outcomes["report_outputs"] == "failure"


# --- defect: a refused artifact set is never followed by a passing check -----


def _drop_receipt(reports: Path) -> None:
    (reports / "verification-receipt.json").unlink()


def _rewrite_receipt_id(reports: Path) -> None:
    receipt = _json(reports / "verification-receipt.json")
    receipt["receipt_id"] = "sha256:" + "f" * 64
    _write_json(reports / "verification-receipt.json", receipt)


def _edit_verdict(reports: Path) -> None:
    verifier = _json(reports / "verifier.json")
    verifier.update(merge_verdict="mergeable", can_merge_without_human=True)
    _write_json(reports / "verifier.json", verifier)


@pytest.mark.parametrize(
    ("sample", "edit", "reason"),
    [
        pytest.param(PASSED, _drop_receipt, "verification-receipt.json is missing",
                     id="missing-receipt"),
        pytest.param(PASSED, _rewrite_receipt_id, "receipt_id must hash", id="tampered-receipt"),
        pytest.param(BLOCKED, _edit_verdict, "hash does not match", id="edited-verdict"),
    ],
)
@pytest.mark.parametrize("cli", ["early-failure", "completed"])
def test_an_artifact_set_the_outputs_step_refuses_never_passes_the_check(
    completed, tmp_path: Path, sample: str, edit, reason: str, cli: str,
) -> None:
    run = completed(sample).copy_to(tmp_path)
    if cli == "early-failure":
        _rerun(run, tmp_path / "rerun", **REFUSED_INPUT)
        assert run.outputs["scan"]["exit_code"] == "2"
    edit(run.reports)

    _assert_no_verdict_under_each_policy(run, tmp_path / "scratch", reason=reason)
    assert run.outcomes["report_outputs"] == "failure"
    assert reason in run.logs[EXTRACT]
    assert run.outputs["report_outputs"] == {}


# --- defect: another head's or repository's evidence is not this run's -------


def _move(run: ActionRun, where: str) -> str:
    """Leave the evidence in place and move what this run evaluates."""

    if where == "head":
        (run.workspace / "NOTES.md").write_text("A later push.\n", encoding="utf-8")
        _git(run.workspace, "commit", "-qam", "later")
        later = _git(run.workspace, "rev-parse", "HEAD").strip()
        run.github["sha"] = later
        run.github["event"]["pull_request"]["head"]["sha"] = later
        return "head commit"
    _git(run.workspace, "remote", "set-url", "origin", "https://github.com/other/repo.git")
    return "repository"


@pytest.mark.parametrize(
    ("cli", "reason"),
    [
        # The realistic case: the new run stopped before it wrote anything.
        pytest.param("early-failure", "is not this run", id="early-failure"),
        # The subject binding on its own: a stand-in that exits 0 and writes
        # nothing, so only the receipt's subject can say whose evidence it is.
        pytest.param("exit-0-stand-in", "the verification receipt names", id="subject"),
    ],
)
@pytest.mark.parametrize("where", ["head", "repository"])
def test_evidence_for_another_head_or_repository_never_passes_the_check(
    completed, tmp_path: Path, cli: str, reason: str, where: str,
) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    named = _move(run, where)
    if cli == "early-failure":
        _rerun(run, tmp_path / "rerun", **REFUSED_INPUT)
    else:
        _rerun(run, tmp_path / "rerun", cli=_stub_cli(tmp_path, "exit 0"))
        reason = f"{reason} {named}"

    _assert_no_verdict_under_each_policy(run, tmp_path / "scratch", reason=reason)
    assert run.outcomes["report_outputs"] == "failure"
    assert reason in run.logs[EXTRACT]


# --- defect: only the payload this run built is posted -----------------------


def _earlier_payload() -> dict[str, Any]:
    return {"name": "Agents Shipgate", "conclusion": "success",
            "output": {"title": "merge_verdict: mergeable", "summary": "earlier",
                       "annotations": []}}


def test_a_payload_already_in_the_directory_is_never_posted(completed, tmp_path: Path) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    _rerun(run, tmp_path / "rerun", check_run_policy="require-mergeable", **REFUSED_INPUT)
    _write_json(run.reports / "check-run-payload.json", _earlier_payload())

    _publish(run, tmp_path / "scratch")

    # The check posted is this run's own, never the payload found on disk.
    _assert_no_verdict(run, "require-mergeable", reason="is not this run")
    assert _posted(run)["output"]["summary"] != "earlier"


def test_the_check_step_posts_only_the_payload_this_run_built(
    completed, tmp_path: Path,
) -> None:
    run = completed(BLOCKED).copy_to(tmp_path)
    run.inputs["check_run_policy"] = "require-mergeable"
    _publish(run, tmp_path / "scratch", (EXTRACT, PAYLOAD))
    assert _json(run.reports / "check-run-payload.json")["conclusion"] == "failure"
    # Replaced between the two steps.
    _write_json(run.reports / "check-run-payload.json", _earlier_payload())

    _publish(run, tmp_path / "scratch", (CREATE,))

    assert run.check_runs == []
    assert run.outcomes[CREATE] == "failure"
    assert any("is not the one this run built" in failure for failure in run.script_failures)


def test_no_check_is_created_when_the_payload_step_wrote_none(completed, tmp_path: Path) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    _write_json(run.reports / "check-run-payload.json", _earlier_payload())
    run.outcomes["check_run_payload"] = "failure"
    run.outputs["check_run_payload"] = {}

    _run_step(run, CREATE, tmp_path / "scratch")

    assert run.outcomes[CREATE] == "skipped"
    assert run.check_runs == []


def test_the_payload_step_writes_a_new_file(completed, tmp_path: Path) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    other = tmp_path / "other.json"
    other.write_text('{"keep": true}\n', encoding="utf-8")
    (run.reports / "check-run-payload.json").symlink_to(other)

    _publish(run, tmp_path / "scratch")

    assert other.read_text(encoding="utf-8") == '{"keep": true}\n'
    payload = run.reports / "check-run-payload.json"
    assert not payload.is_symlink()
    assert _json(payload)["conclusion"] == "success"
    assert _posted(run) == {**_json(payload), "owner": "owner", "repo": "repo",
                            "head_sha": run.pr_head, "status": "completed"}


# --- defect: the check passes only on the receipt the outputs step validated ---


def test_success_needs_the_receipt_the_outputs_step_validated(completed, tmp_path: Path) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    run.inputs["check_run_policy"] = "require-mergeable"
    _publish(run, tmp_path / "scratch", (EXTRACT,))
    assert run.outcomes["report_outputs"] == "success"
    for receipt_id in ("", "sha256:" + "0" * 64):
        run.outputs["report_outputs"]["receipt_id"] = receipt_id
        run.check_runs.clear()
        _publish(run, tmp_path / "scratch", (PAYLOAD, CREATE))
        _assert_no_verdict(run, "require-mergeable", reason="did not validate this receipt")


# --- defect: after a CLI that did not complete, no outcome is this run's ------


def test_an_outcome_no_receipt_binds_is_not_this_runs_after_an_early_failure(
    completed, tmp_path: Path,
) -> None:
    """A receipt-less verifier stating an outcome is refused, whatever version it names."""

    run = completed(PASSED).copy_to(tmp_path)
    _rerun(run, tmp_path / "rerun", **REFUSED_INPUT)
    (run.reports / "verification-receipt.json").unlink()
    verifier = _json(run.reports / "verifier.json")
    for field_name in ("request_id", "subject_id", "input_set_id", "engine_requirement_id",
                       "executor_id", "decision_id", "head_report_json"):
        verifier.pop(field_name, None)
    verifier["artifacts"].pop("report_json", None)
    verifier.update(verifier_schema_version="0.4", merge_verdict="mergeable",
                    can_merge_without_human=True)
    _write_json(run.reports / "verifier.json", verifier)

    _assert_no_verdict_under_each_policy(run, tmp_path / "scratch", reason="is not this run")
    assert run.outcomes["report_outputs"] == "failure"
    assert run.outputs["report_outputs"] == {}


# --- every step that publishes the directory reads the same validation ------


def _enable_every_publisher(run: ActionRun) -> None:
    run.inputs.update(attestation="true", org_bundle="true", pr_comment="true",
                      check_run_policy="require-mergeable")


def test_a_complete_run_feeds_every_publishing_step(completed, tmp_path: Path) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    _enable_every_publisher(run)

    _publish(run, tmp_path / "scratch", EVERY_PUBLISHER)

    receipt = _json(run.reports / "verification-receipt.json")
    assert run.outputs["report_outputs"]["evidence"] == "validated"
    assert _json(run.reports / "attestation.json")["receipt_id"] == receipt["receipt_id"]
    assert (run.reports / "org-evidence-bundle.json").is_file(), run.logs[BUNDLE]
    assert run.outcomes[ANNOTATE] == "success"
    [comment] = run.pr_comments
    review = (run.reports / "pr-comment.md").read_text(encoding="utf-8")
    assert comment["body"].startswith(review[:200])
    assert _posted(run)["conclusion"] == "success"


def _earlier_attestation_and_bundle(run: ActionRun) -> None:
    """An attestation and an org bundle already in the directory, not this run's."""

    _write_json(run.reports / "attestation.json", {"receipt_id": "sha256:" + "e" * 64})
    _write_json(run.reports / "org-evidence-bundle.json", {"receipt_id": "sha256:" + "e" * 64})


def test_after_an_early_cli_failure_no_step_publishes_the_directory(
    completed, tmp_path: Path,
) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    _rerun(run, tmp_path / "rerun", **REFUSED_INPUT)
    _enable_every_publisher(run)
    _earlier_attestation_and_bundle(run)

    _publish(run, tmp_path / "scratch", EVERY_PUBLISHER)

    assert not (run.reports / "attestation.json").exists()
    assert "attestation skipped: this run has no validated result" in run.logs[ATTEST]
    assert not (run.reports / "org-evidence-bundle.json").exists()
    assert "org bundle skipped: this run has no validated result" in run.logs[BUNDLE]
    assert run.outcomes["report_outputs"] == "failure"
    assert run.outcomes[ANNOTATE] == "skipped"
    [comment] = run.pr_comments
    assert comment["body"].startswith("<!-- agents-shipgate-pr-comment -->")
    assert NO_RESULT_NOTE in comment["body"]
    earlier_review = (run.reports / "pr-comment.md").read_text(encoding="utf-8")
    assert earlier_review[40:200] not in comment["body"]
    _assert_no_verdict(run, "require-mergeable", reason="is not this run")


def _malform_the_head(run: ActionRun) -> None:
    """A new head whose tool source does not parse, so the head scan exits 3."""

    (run.workspace / "tools.json").write_text("{ not json\n", encoding="utf-8")
    _git(run.workspace, "commit", "-qam", "unparseable tools")
    later = _git(run.workspace, "rev-parse", "HEAD").strip()
    run.github["sha"] = later
    run.github["event"]["pull_request"]["head"]["sha"] = later


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [pytest.param("missing-config", "2", id="missing-config"),
     pytest.param("head-scan-error", "3", id="head-scan-error")],
)
def test_a_fresh_failed_run_publishes_its_unknown_outputs_and_nothing_from_the_directory(
    completed, tmp_path: Path, failure: str, exit_code: str,
) -> None:
    """A run that failed on its own writes a verifier stating no outcome.

    Its outputs still publish, as `unknown` with no receipt. Being a run that
    did not complete, it has no result for any other step to publish.
    """

    run = completed(PASSED).copy_to(tmp_path)
    if failure == "missing-config":
        _rerun(run, tmp_path / "rerun", config="missing.yaml")
    else:
        _malform_the_head(run)
        _rerun(run, tmp_path / "rerun")
    assert run.outputs["scan"]["exit_code"] == exit_code, run.logs
    verifier = _json(run.reports / "verifier.json")
    assert verifier["execution"] == "failed"
    assert not (run.reports / "verification-receipt.json").exists()
    _enable_every_publisher(run)
    _earlier_attestation_and_bundle(run)

    _publish(run, tmp_path / "scratch", EVERY_PUBLISHER)

    assert run.outcomes["report_outputs"] == "success", run.logs[EXTRACT]
    # The fixed no-result values, every one of them, read from nothing.
    assert run.outputs["report_outputs"] == {**_no_result_outputs(), "evidence": "no_result"}
    summary = (tmp_path / "scratch" / "step-summary").read_text(encoding="utf-8")
    assert f"No validated verification result: agents-shipgate exited {exit_code}." in summary
    assert verifier["control"]["reason"] not in summary
    assert not (run.reports / "attestation.json").exists()
    assert not (run.reports / "org-evidence-bundle.json").exists()
    assert run.outcomes[ANNOTATE] == "skipped"
    [comment] = run.pr_comments
    assert NO_RESULT_NOTE in comment["body"]
    _assert_no_verdict(run, "require-mergeable", reason=f"exited {exit_code}")


def _no_result_outputs() -> dict[str, str]:
    """`no_result_outputs()` as the step writes it to GITHUB_OUTPUT."""

    from scripts.github_action_outputs import clean, no_result_outputs

    return {key: clean(value) for key, value in no_result_outputs().items()}


#: What a run that did not complete may publish about merge authority.
NO_AUTHORITY = {
    "merge_verdict": "unknown",
    "can_merge_without_human": "false",
    "agent_control_state": "human_review_required",
    "agent_controller_must_stop": "true",
    "agent_controller_completion_allowed": "false",
    "receipt_id": "",
    "attestation_json": "",
    "org_evidence_bundle_json": "",
    "pr_comment_markdown": "",
}


def _as_receiptless(run: ActionRun, variant: str) -> None:
    """Rewrite the earlier passing set as one with no receipt and no stated verdict.

    Each keeps a control that authorizes completion: the current `control`
    block under a pre-receipt label, a legacy `agent_controller` block, or no
    version label at all.
    """

    for name in ("verification-receipt.json", "report.json"):
        (run.reports / name).unlink(missing_ok=True)
    verifier = _json(run.reports / "verifier.json")
    for name in ("request_id", "subject_id", "input_set_id", "engine_requirement_id",
                 "executor_id", "decision_id", "head_report_json", "decision",
                 "release_decision", "can_merge_without_human", "applicability"):
        verifier.pop(name, None)
    verifier["artifacts"].pop("report_json", None)
    verifier.update(merge_verdict="unknown", execution="skipped", head_status="skipped")
    assert verifier["control"]["state"] == "complete"
    if variant == "control":
        verifier["verifier_schema_version"] = "0.4"
    elif variant == "agent-controller":
        verifier["verifier_schema_version"] = "0.4"
        control = verifier.pop("control")
        verifier["agent_controller"] = {
            "state": "complete", "reason": control["reason"], "must_stop": False,
            "completion_allowed": True, "stop_reason": None,
        }
    else:
        verifier.pop("verifier_schema_version")
        verifier.pop("execution")
        verifier.pop("head_status")
    _write_json(run.reports / "verifier.json", verifier)


@pytest.mark.parametrize("variant", ["control", "agent-controller", "unlabelled"])
def test_a_run_that_did_not_complete_publishes_no_merge_authority(
    completed, tmp_path: Path, variant: str,
) -> None:
    run = completed(PASSED).copy_to(tmp_path)
    _rerun(run, tmp_path / "rerun", **REFUSED_INPUT)
    _as_receiptless(run, variant)
    _enable_every_publisher(run)

    _publish(run, tmp_path / "scratch", EVERY_PUBLISHER)

    outputs = run.outputs.get("report_outputs", {})
    if run.outcomes["report_outputs"] == "failure":
        assert outputs == {}
    else:
        assert outputs == {**_no_result_outputs(), "evidence": "no_result"}
        assert {key: outputs[key] for key in NO_AUTHORITY} == NO_AUTHORITY
    assert not (run.reports / "attestation.json").exists()
    assert not (run.reports / "org-evidence-bundle.json").exists()
    assert run.outcomes[ANNOTATE] == "skipped"
    [comment] = run.pr_comments
    assert NO_RESULT_NOTE in comment["body"]
    _assert_no_verdict_under_each_policy(
        run, tmp_path / "scratch", reason="This run has no validated verification result"
    )


def test_the_evidence_binding_reads_the_head_the_run_step_passed(tmp_path: Path) -> None:
    """`ActionInvocation` selects the run step's own `--head`, for each input shape."""

    from scripts.github_action_outputs import ActionInvocation

    recorder = _stub_cli(tmp_path, f'printf "%s\\n" "$@" > "{tmp_path / "argv"}"')
    step = _step(RUN)
    cases = [("", "a" * 40), ("feature", "a" * 40), (" padded ", "b" * 40), ("feature", "")]
    for head_ref, sha in cases:
        env = {**_clean_env(), **dict.fromkeys(step["env"], "")}
        env.update({
            "SHIPGATE_BIN": str(recorder), "VERIFY_MODE": "verify", "APPLICATION": "false",
            "CONFIG": "shipgate.yaml", "CI_MODE": "advisory", "OUTPUT_DIR": "reports",
            "PR_COMMENT_STYLE": "capability-review", "INPUT_HEAD_REF": head_ref,
            "EVALUATED_HEAD_SHA": sha, "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        })
        subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env, check=True)
        argv = (tmp_path / "argv").read_text(encoding="utf-8").splitlines()
        passed = argv[argv.index("--head") + 1] if "--head" in argv else ""
        assert ActionInvocation.from_env(env).head == passed, (head_ref, sha, argv)


def test_every_reader_of_the_evidence_sees_what_the_run_step_did() -> None:
    """Each step that validates the directory gets the run step's head and exit
    code by the same expressions; the steps after the outputs step read its
    `evidence`, and the check step depends on the payload step's digest."""

    run, extract, payload, create = (_step(name) for name in (RUN, EXTRACT, PAYLOAD, CREATE))
    for reader in (_step(ATTEST), _step(BUNDLE), extract, payload):
        for variable in ("INPUT_HEAD_REF", "EVALUATED_HEAD_SHA"):
            assert reader["env"][variable] == run["env"][variable]
        assert reader["env"]["SHIPGATE_EXIT_CODE"] == "${{ steps.scan.outputs.exit_code }}"
    for checker, artifact in ((_step(ATTEST), "attestation.json"),
                              (_step(BUNDLE), "org-evidence-bundle.json")):
        assert 'scripts/github_action_outputs.py" --check-evidence' in checker["run"]
        # Removed before anything else, so only this run's can be left.
        removal = f'rm -f -- "${{OUTPUT_DIR}}/{artifact}"'
        assert checker["run"].index(removal) < checker["run"].index("--check-evidence")
    # The upload is the directory as found, and says so.
    assert "nothing in it is validated" in ACTION["inputs"]["upload_artifact"]["description"]
    assert "steps.report_outputs.outputs.evidence == 'validated'" in _step(ANNOTATE)["if"]
    assert _step(COMMENT)["env"]["EVIDENCE"] == "${{ steps.report_outputs.outputs.evidence }}"
    assert payload["env"]["VALIDATED_RECEIPT_ID"] == "${{ steps.report_outputs.outputs.receipt_id }}"
    assert payload["id"] == "check_run_payload"
    # Both still run after a failure, so that a failing check is published.
    assert payload["if"].startswith("${{ always() && ")
    assert create["if"].startswith("${{ always() && ")
    assert "steps.check_run_payload.outputs.payload_sha256 != ''" in create["if"]
    assert create["env"]["PAYLOAD_SHA256"] == "${{ steps.check_run_payload.outputs.payload_sha256 }}"
    order = [step.get("name") for step in STEPS]
    assert [order.index(name) for name in (RUN, *EVERY_PUBLISHER)] == sorted(
        order.index(name) for name in (RUN, *EVERY_PUBLISHER)
    )
