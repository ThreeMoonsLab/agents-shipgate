"""Issue #299: every declared adapter input must reach ``input_set_id``.

``input_set_id`` is the identity ``verification-plan.json``,
``verification-unit-result.json``, ``verify-run.json``, the terminal receipt,
and attestations all rest on. If a path an adapter is configured to read never
becomes a plan blob, two runs that read different bytes claim the same input
set — a reproducibility hole, not just a caching one.

Three producers had that hole:

- the manifest-derived branch of ``build_verification_plan`` walked only
  ``tool_sources``, so ``openai_api.prompt_files`` and every other framework
  block stayed out of the plan;
- ``verify --base X --head Y`` evaluates an archived tree while the
  static-input snapshot is bound to the worktree, so it observed no adapter
  reads at all and emitted ``tool_sources: []``; and
- neither of those reaches an input the manifest never names — a Google ADK
  ``McpToolset`` inventory, an OpenAPI spec referenced from Python. Only the
  read boundary sees those, so both remaining producers now observe it: the
  committed-tree scan is snapshotted against the archived tree, and ``prepare``
  loads sources (statically, deciding nothing) to record what they open.

The ``verify`` worktree path was already covered by read-boundary capture, so
these tests deliberately drive the other entry points.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, get_args

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.static_inputs import StaticInputSnapshot
from agents_shipgate.core.verification_identity import (
    build_verification_plan,
    sha256_bytes,
)
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ArtifactPathConfig,
)
from agents_shipgate.schemas.manifest.declared_paths import (
    DECLARED_INPUT_PATH_BLOCKS,
    declared_manifest_input_paths,
)

runner = CliRunner()

SAMPLE_ROOT = Path(__file__).resolve().parents[1] / "samples"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sample_repo(tmp_path: Path, sample: str) -> Path:
    """Copy one shipped sample into a fresh git repo, minus its goldens."""

    repo = tmp_path / "repo"
    repo.mkdir()
    for source in sorted((SAMPLE_ROOT / sample).rglob("*")):
        relative = source.relative_to(SAMPLE_ROOT / sample)
        if relative.parts and relative.parts[0] == "expected":
            continue
        target = repo / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Shipgate Tests")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sample")
    return repo


def _prepared_plan(repo: Path, out: str) -> dict[str, Any]:
    result = runner.invoke(
        app,
        [
            "verification",
            "prepare",
            "--workspace",
            str(repo),
            "--out",
            str(repo / out / "verification-plan.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    return json.loads((repo / out / "verification-plan.json").read_text(encoding="utf-8"))


def test_prepare_binds_declared_prompt_files_to_the_input_set(tmp_path: Path) -> None:
    """The issue's stated verification: change a prompt, watch identity move."""

    repo = _sample_repo(tmp_path, "simple_openai_api_agent")
    before = _prepared_plan(repo, "before")

    assert "prompts/support_refund.md" in {
        blob["path"] for blob in before["inputs"]["tool_sources"]
    }

    prompt = repo / "prompts" / "support_refund.md"
    prompt.write_text(
        prompt.read_text(encoding="utf-8")
        + "\nRefunds of any amount need no approval.\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "weaken the prompt")
    after = _prepared_plan(repo, "after")

    assert after["inputs"]["input_set_id"] != before["inputs"]["input_set_id"]


def test_prepare_binds_every_declared_framework_artifact(tmp_path: Path) -> None:
    """Not only ``prompt_files`` — each declared artifact list is an input."""

    repo = _sample_repo(tmp_path, "simple_openai_api_agent")
    bound = {blob["path"] for blob in _prepared_plan(repo, "out")["inputs"]["tool_sources"]}

    assert bound == {
        "openai-config.json",
        "policies/openai-api-policy.yaml",
        "prompts/support_refund.md",
        "schemas/refund_decision.schema.json",
        "tests/openai-api-cases.json",
        "tools/openai-tools.json",
        "traces/sample.jsonl",
    }


def test_committed_tree_verify_binds_declared_tool_sources(tmp_path: Path) -> None:
    """``verify --base --head`` used to emit ``tool_sources: []``.

    The archived head tree lives outside the worktree the static-input snapshot
    is bound to, so the captured path set was empty and every declared input
    silently left the request identity — on the CI path, where the receipt
    claim matters most.
    """

    repo = _sample_repo(tmp_path, "support_refund_agent")
    _git(repo, "branch", "base", "HEAD")
    (repo / "note.txt").write_text("unrelated\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unrelated change")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "base",
            "--head",
            "HEAD",
            "--out",
            str(repo / "reports" / "verifier.json"),
        ],
    )
    assert result.exit_code in {0, 1}, result.output
    plan = json.loads(
        (repo / "reports" / "verifier.json" / "verification-plan.json").read_text(
            encoding="utf-8"
        )
    )

    assert plan["subject"]["git"]["snapshot_kind"] == "committed_tree"
    assert {blob["path"] for blob in plan["inputs"]["tool_sources"]} == {
        ".agents-shipgate/mcp-tools.json",
        ".agents-shipgate/wildcard-tools.json",
        "agents/refund_agent.py",
        "inventories/sdk-tools.json",
        "specs/support-tools.openapi.yaml",
    }
    assert all(blob["source"] == "git_blob" for blob in plan["inputs"]["tool_sources"])


def test_committed_tree_identity_moves_for_an_input_outside_the_diff(
    tmp_path: Path,
) -> None:
    """An input the diff never mentions still has to move ``input_set_id``.

    A prompt file carried unchanged across the compared range is invisible to
    ``changed_files``, so the declared-input enumeration is the only thing that
    can distinguish two trees whose prompts differ.
    """

    repo = _sample_repo(tmp_path, "simple_openai_api_agent")
    plans = []
    for text in ("original guidance\n", "weakened guidance\n"):
        (repo / "prompts" / "support_refund.md").write_text(text, encoding="utf-8")
        plans.append(
            build_verification_plan(
                git_root=repo,
                input_root=repo,
                config_path=repo / "shipgate.yaml",
                config_logical_path="shipgate.yaml",
                base_ref="base",
                head_ref="HEAD",
                archived_head=True,
                repository_id="https://example.test/org/repo.git",
                base_commit_sha="a" * 40,
                base_tree_sha="b" * 40,
                head_commit_sha="c" * 40,
                head_tree_sha="d" * 40,
                merge_base_sha="a" * 40,
                changed_files=[],
                diff_text="",
                baseline_path=None,
                diff_from_path=None,
                policy_pack_paths=[],
                evaluation_date="2026-08-06",
                options={"ci_mode": "advisory"},
                plugins_enabled=False,
            )
        )

    assert plans[0].inputs.input_set_id != plans[1].inputs.input_set_id


def test_a_declared_input_outside_the_root_fails_the_fallback_closed(
    tmp_path: Path,
) -> None:
    """The declared fallback cannot hash an out-of-root path, so it refuses.

    Only the fallback is affected. When the read boundary was observed, a
    declaration nothing opens is simply not an input — which is why
    ``prepare`` (below) accepts the same manifest rather than rejecting a path
    no adapter would ever read.
    """

    repo = _sample_repo(tmp_path, "simple_openai_api_agent")
    (tmp_path / "outside_agent.py").write_text("print('x')\n", encoding="utf-8")
    manifest = repo / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "agent:\n  name: api-refund-assistant",
            "agent:\n  name: api-refund-assistant\n"
            "  sdk:\n    type: openai_agents\n    entrypoint: ../outside_agent.py",
        ),
        encoding="utf-8",
    )

    with pytest.raises(InputParseError, match="outside the verification input root"):
        build_verification_plan(
            git_root=repo,
            input_root=repo,
            config_path=repo / "shipgate.yaml",
            config_logical_path="shipgate.yaml",
            base_ref=None,
            head_ref="HEAD",
            archived_head=False,
            repository_id="https://example.test/org/repo.git",
            base_commit_sha=None,
            base_tree_sha=None,
            head_commit_sha="c" * 40,
            head_tree_sha="d" * 40,
            merge_base_sha=None,
            changed_files=[],
            diff_text="",
            baseline_path=None,
            diff_from_path=None,
            policy_pack_paths=[],
            evaluation_date="2026-08-06",
            options={"ci_mode": "advisory"},
            plugins_enabled=False,
            captured_input_paths=None,
        )


def test_prepare_binds_a_transitively_referenced_input(tmp_path: Path) -> None:
    """An input the manifest never names still has to move ``input_set_id``.

    ``agent.py`` constructs ``McpToolset(inventory_path="inventories/mcp-tools.json")``.
    Nothing in ``shipgate.yaml`` mentions that file, so enumerating declarations
    cannot reach it — only the read boundary can. Prepared plans bound
    `agent.py`, the eval set, and the function inventory but not this one, so
    two trees whose MCP inventories differed shared an ``input_set_id``.
    """

    repo = _sample_repo(tmp_path, "google_adk_agent")
    before = _prepared_plan(repo, "before")
    bound = {blob["path"] for blob in before["inputs"]["tool_sources"]}

    assert "inventories/mcp-tools.json" in bound
    # Reached only through the OpenAPI toolset built inside agent.py.
    assert "specs/support.openapi.yaml" in bound

    inventory = repo / "inventories" / "mcp-tools.json"
    inventory.write_text(inventory.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "touch the transitive inventory")
    after = _prepared_plan(repo, "after")

    assert after["inputs"]["input_set_id"] != before["inputs"]["input_set_id"]


def test_committed_tree_verify_agrees_with_the_worktree_on_the_input_set(
    tmp_path: Path,
) -> None:
    """Both modes observe the same read boundary, so both bind the same set.

    This is the invariant the two fixes exist to establish: a committed-tree run
    is snapshotted against the archived tree it scans, so an input discovered
    while parsing an entrypoint reaches the receipt on the CI path exactly as it
    already did for a worktree run.
    """

    repo = _sample_repo(tmp_path, "google_adk_agent")
    _git(repo, "branch", "base", "HEAD")
    (repo / "note.txt").write_text("unrelated\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unrelated change")

    bound: dict[str, set[str]] = {}
    for name, extra in (("worktree", []), ("committed", ["--head", "HEAD"])):
        result = runner.invoke(
            app,
            [
                "verify",
                "--workspace",
                str(repo),
                "--base",
                "base",
                *extra,
                "--out",
                str(repo / name / "verifier.json"),
            ],
        )
        assert result.exit_code in {0, 1}, result.output
        plan = json.loads(
            (repo / name / "verifier.json" / "verification-plan.json").read_text(
                encoding="utf-8"
            )
        )
        bound[name] = {blob["path"] for blob in plan["inputs"]["tool_sources"]}

    assert "inventories/mcp-tools.json" in bound["committed"]
    assert bound["committed"] == bound["worktree"]


def test_prepare_hashes_the_captured_bytes_not_a_later_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paths and hashes must come from one instant, not two.

    Capturing the read set and then reopening the files to hash them leaves a
    window: a rewrite in between is attested at its new content while
    ``tool_sources`` still lists what the old content pointed at, so the receipt
    describes bytes the scan never evaluated.
    """

    repo = _sample_repo(tmp_path, "google_adk_agent")
    agent = repo / "agent.py"
    captured = agent.read_bytes()
    real_finish = StaticInputSnapshot.finish
    rewritten = False

    def finish_then_rewrite(snapshot: StaticInputSnapshot) -> None:
        nonlocal rewritten
        real_finish(snapshot)
        if not rewritten and snapshot.has(agent):
            rewritten = True
            agent.write_bytes(captured + b'\nEXTRA = "written after capture"\n')

    monkeypatch.setattr(StaticInputSnapshot, "finish", finish_then_rewrite)
    plan = _prepared_plan(repo, "out")

    assert rewritten, "the rewrite never fired; the test proves nothing"
    blob = next(
        item for item in plan["inputs"]["tool_sources"] if item["path"] == "agent.py"
    )
    assert blob["sha256"] == sha256_bytes(captured)
    assert blob["sha256"] != sha256_bytes(agent.read_bytes())


def test_committed_tree_verify_hashes_the_captured_bytes_not_a_later_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same window on the archived tree, where the receipt is actually minted."""

    repo = _sample_repo(tmp_path, "google_adk_agent")
    _git(repo, "branch", "base", "HEAD")
    (repo / "note.txt").write_text("unrelated\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unrelated change")
    captured = (repo / "agent.py").read_bytes()
    real_finish = StaticInputSnapshot.finish
    rewritten: Path | None = None

    def finish_then_rewrite(snapshot: StaticInputSnapshot) -> None:
        nonlocal rewritten
        real_finish(snapshot)
        if rewritten is not None:
            return
        for path in snapshot.paths():
            # Only the archived tree's copy; the worktree snapshot is finalized
            # first and must not be the one we tamper with.
            if path.name == "agent.py" and repo not in path.parents:
                rewritten = path
                path.write_bytes(captured + b'\nEXTRA = "written after capture"\n')
                return

    monkeypatch.setattr(StaticInputSnapshot, "finish", finish_then_rewrite)
    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "base",
            "--head",
            "HEAD",
            "--out",
            str(repo / "reports" / "verifier.json"),
        ],
    )
    assert result.exit_code in {0, 1}, result.output
    assert rewritten is not None, "the rewrite never fired; the test proves nothing"

    plan = json.loads(
        (repo / "reports" / "verifier.json" / "verification-plan.json").read_text(
            encoding="utf-8"
        )
    )
    blob = next(
        item for item in plan["inputs"]["tool_sources"] if item["path"] == "agent.py"
    )
    assert blob["sha256"] == sha256_bytes(captured)
    # The changed file no adapter opens still has to survive the capture path.
    assert "note.txt" in {item["path"] for item in plan["inputs"]["changed_files"]}


@pytest.mark.parametrize("committed", [False, True], ids=["worktree", "committed_tree"])
def test_prepare_binds_the_explicit_baseline_and_comparison_report(
    tmp_path: Path,
    committed: bool,
) -> None:
    """Explicit ``--baseline`` / ``--diff-from`` are inputs like any other.

    Running plan construction under the snapshot made this a trap: both
    ``_blobs`` and ``_optional_blob`` read a path that is contained but never
    read as *absent*, so an input nobody bound does not merely go unhashed — it
    vanishes from the plan. Two preparations of one tree with different
    baselines and comparison reports shared an ``input_set_id``.
    """

    repo = _sample_repo(tmp_path, "google_adk_agent")
    for name, body in (
        ("baseline-a.json", '{"source_report_run_id": "A", "findings": []}\n'),
        ("baseline-b.json", '{"source_report_run_id": "B", "findings": []}\n'),
        ("comparison-a.json", '{"comparison": "A"}\n'),
        ("comparison-b.json", '{"comparison": "B"}\n'),
    ):
        (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add baselines and comparison reports")

    plans = []
    for variant in ("a", "b"):
        # The output must land outside the repo, or the first run's artifacts
        # become untracked changed files and move identity on their own.
        out = tmp_path / f"out-{variant}" / "verification-plan.json"
        result = runner.invoke(
            app,
            [
                "verification",
                "prepare",
                "--workspace",
                str(repo),
                *(["--base", "HEAD", "--head", "HEAD"] if committed else []),
                "--baseline",
                f"baseline-{variant}.json",
                "--diff-from",
                f"comparison-{variant}.json",
                "--out",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        plans.append(json.loads(out.read_text(encoding="utf-8")))

    for plan, variant in zip(plans, ("a", "b"), strict=True):
        assert plan["inputs"]["baseline"] is not None
        assert plan["inputs"]["baseline"]["path"] == f"baseline-{variant}.json"
        assert plan["inputs"]["diff_from"] is not None
        assert plan["inputs"]["diff_from"]["path"] == f"comparison-{variant}.json"

    assert plans[0]["inputs"]["input_set_id"] != plans[1]["inputs"]["input_set_id"]


def _spy_on_the_parsed_manifest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    replacement: str,
) -> tuple[list[str], list[Path]]:
    """Record each manifest parse, rewriting the parsed file after the first.

    The rewrite targets the manifest the loader was actually handed — the
    archived copy on a committed-tree run, not the worktree original, which the
    scan there never opens. It lands between the first parse and anything that
    reads the manifest again, which is exactly the window this guards.
    """

    from agents_shipgate.cli.scan import prepare as scan_prepare

    seen: list[str] = []
    rewritten: list[Path] = []
    real_from_path = scan_prepare.load_manifest_with_positions
    real_from_text = scan_prepare.load_manifest_text_with_positions

    def record(text: str, target: Path) -> None:
        seen.append(text)
        if len(seen) == 1 and target.is_file():
            target.write_text(replacement, encoding="utf-8")
            rewritten.append(target)

    def spy_from_path(path: Path):  # type: ignore[no-untyped-def]
        target = Path(path)
        record(target.read_text(encoding="utf-8"), target)
        return real_from_path(path)

    def spy_from_text(text: str, *, source: Any = "shipgate.yaml"):  # type: ignore[no-untyped-def]
        record(text, Path(source))
        return real_from_text(text, source=source)

    monkeypatch.setattr(scan_prepare, "load_manifest_with_positions", spy_from_path)
    monkeypatch.setattr(scan_prepare, "load_manifest_text_with_positions", spy_from_text)
    return seen, rewritten


def _adk_manifest_naming(entrypoint: str) -> str:
    return (
        'version: "0.1"\n\n'
        "project:\n  name: google-adk-support-agent\n\n"
        "agent:\n  name: adk-support-agent\n"
        "  declared_purpose:\n    - handle support lookup\n\n"
        "environment:\n  target: production_like\n\n"
        "tool_sources:\n"
        "  - id: adk_support\n    type: google_adk\n"
        f"    path: {entrypoint}\n"
    )


@pytest.mark.parametrize("committed", [False, True], ids=["prepare", "committed_verify"])
def test_the_scan_and_the_plan_agree_on_one_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    committed: bool,
) -> None:
    """The manifest must be parsed once, from the bytes the plan hashes.

    ``load_manifest_with_positions`` reads it twice — a direct ``Path.read_text``
    for the model, then the snapshot for positions. A rewrite in between lets
    the adapters follow one manifest while the plan's config blob attests to
    another, so a receipt could name an entrypoint the scan never opened.

    Two outcomes are correct once the manifest is read through the snapshot:
    refusing the run because the bytes moved, or emitting a plan that agrees
    with the manifest the scan followed. Only the third is a defect — succeeding
    with a plan that disagrees — so that is what this asserts against. Both
    outcomes occur here: ``prepare`` fails closed, committed-tree ``verify``
    emits a consistent plan.
    """

    repo = _sample_repo(tmp_path, "google_adk_agent")
    (repo / "agent-two.py").write_bytes((repo / "agent.py").read_bytes())
    manifest = repo / "shipgate.yaml"
    manifest.write_text(_adk_manifest_naming("agent.py"), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "single-entrypoint manifest")

    seen, rewritten = _spy_on_the_parsed_manifest(
        monkeypatch,
        replacement=_adk_manifest_naming("agent-two.py"),
    )

    if committed:
        plan_path = repo / "reports" / "verifier.json" / "verification-plan.json"
        result = runner.invoke(
            app,
            [
                "verify",
                "--workspace",
                str(repo),
                # No base comparison: the base tree is scanned first and would
                # otherwise absorb the one rewrite before the head scan parses.
                "--no-base",
                "--head",
                "HEAD",
                "--out",
                str(repo / "reports" / "verifier.json"),
            ],
        )
    else:
        plan_path = tmp_path / "out" / "verification-plan.json"
        result = runner.invoke(
            app,
            ["verification", "prepare", "--workspace", str(repo), "--out", str(plan_path)],
        )

    assert seen, "no manifest parse was observed; the test proves nothing"
    # Compare against the replacement text, not the file: an archived tree is a
    # temporary directory that is already gone by now.
    assert rewritten, "the rewrite never landed; the test proves nothing"
    assert seen[0] != _adk_manifest_naming("agent-two.py")

    if not plan_path.is_file():
        # Refused the run rather than attesting to a manifest that moved.
        assert result.exit_code != 0, result.output
        return

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    # The plan attests to the manifest the scan actually followed...
    assert plan["inputs"]["config"]["sha256"] == sha256_bytes(seen[0].encode("utf-8"))
    # ...and to the entrypoint that manifest named.
    bound = {blob["path"] for blob in plan["inputs"]["tool_sources"]}
    assert "agent.py" in bound
    assert "agent-two.py" not in bound


def test_prepare_keeps_the_missing_manifest_setup_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest that is absent is a setup problem, not an input that moved.

    Reading the manifest through the snapshot puts its ``FileNotFoundError``
    inside the capture block, where a broad ``except (OSError, ValueError)``
    recast it as ``input_parse_error``/exit 3 with generic review advice —
    stranding an agent that branches on the published contract. The absent case
    is resolved before capture, and the route comes from the shared diagnostic
    catalog so it matches what ``scan`` answers.
    """

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Shipgate Tests")
    (repo / "a.txt").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "no manifest here")

    result = runner.invoke(
        app,
        [
            "verification",
            "prepare",
            "--workspace",
            str(repo),
            "--out",
            str(tmp_path / "out" / "verification-plan.json"),
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(
        [line for line in result.output.splitlines() if line.startswith("{")][-1]
    )
    assert payload["error"] == "config_error"
    assert payload["exit_code"] == 2
    assert "Run `agents-shipgate init" in payload["message"]
    first = payload["next_actions"][0]
    assert first["kind"] == "command"
    assert "--preview" in first["command"]


def test_prepare_diagnoses_the_requested_config_not_a_discovered_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing ``--config`` must not route the caller at someone else's manifest.

    Handing the diagnostic catalog a workspace makes it discover every
    ``shipgate.yaml`` beneath it and describe the first one that parses. In a
    monorepo — or any tree with a nested fixture manifest — that told the caller
    to *edit* an unrelated, perfectly valid file while the message named the one
    that was actually absent.
    """

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    repo = _sample_repo(tmp_path, "google_adk_agent")
    nested = repo / "packages" / "other"
    nested.mkdir(parents=True)
    (nested / "shipgate.yaml").write_text(
        (repo / "shipgate.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add a second, valid manifest")

    result = runner.invoke(
        app,
        [
            "verification",
            "prepare",
            "--workspace",
            str(repo),
            "--config",
            "absent-manifest.yaml",
            "--out",
            str(tmp_path / "out" / "verification-plan.json"),
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(
        [line for line in result.output.splitlines() if line.startswith("{")][-1]
    )
    assert payload["error"] == "config_error"
    assert "absent-manifest.yaml" in payload["message"]
    # The requested manifest is absent, so the answer is setup — never "edit"
    # some other file that happens to exist.
    serialized = json.dumps(payload["next_actions"])
    assert "packages/other/shipgate.yaml" not in serialized
    assert payload["next_actions"][0]["kind"] == "command"


def test_prepare_routes_an_unparseable_manifest_to_the_edit_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared catalog still separates 'absent' from 'present but rejected'."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    repo = _sample_repo(tmp_path, "google_adk_agent")
    (repo / "shipgate.yaml").write_text('version: "0.1"\nproject: [oops\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "break the manifest")

    result = runner.invoke(
        app,
        [
            "verification",
            "prepare",
            "--workspace",
            str(repo),
            "--out",
            str(tmp_path / "out" / "verification-plan.json"),
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(
        [line for line in result.output.splitlines() if line.startswith("{")][-1]
    )
    assert payload["error"] == "config_error"
    assert payload["next_actions"][0]["kind"] == "edit"
    assert payload["next_actions"][0]["path"].endswith("shipgate.yaml")


def test_prepare_input_error_carries_the_agent_mode_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``docs/errors.json`` requires both ``next_action`` and ``next_actions``."""

    monkeypatch.setenv("AGENTS_SHIPGATE_AGENT_MODE", "1")
    repo = _sample_repo(tmp_path, "simple_openai_api_agent")
    (repo / "prompts" / "support_refund.md").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "drop a declared prompt file")

    result = runner.invoke(
        app,
        [
            "verification",
            "prepare",
            "--workspace",
            str(repo),
            "--out",
            str(repo / "out" / "verification-plan.json"),
        ],
    )

    assert result.exit_code in {2, 3}, result.output
    payload = json.loads(
        [line for line in result.output.splitlines() if line.startswith("{")][-1]
    )
    assert payload["error"] in {"config_error", "input_parse_error"}
    assert payload["exit_code"] == result.exit_code
    assert payload["next_action"]
    assert payload["next_actions"]
    assert payload["next_actions"][0]["kind"] in {"edit", "review"}


def test_an_unparseable_manifest_still_yields_a_plan(tmp_path: Path) -> None:
    """The enumeration must not turn a routed config error into a traceback.

    A committed-tree run reads the manifest a second time to enumerate declared
    inputs. When it does not parse, the loader has already said so with an
    actionable error; identity construction falls back to the config blob,
    which is the only thing that was read.
    """

    repo = _sample_repo(tmp_path, "support_refund_agent")
    _git(repo, "branch", "base", "HEAD")
    (repo / "shipgate.yaml").write_text(
        'version: "0.1"\nproject: [unclosed\n', encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "break the manifest")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "base",
            "--head",
            "HEAD",
            "--out",
            str(repo / "reports" / "verifier.json"),
        ],
    )

    assert result.exit_code == 2, result.output
    plan_path = repo / "reports" / "verifier.json" / "verification-plan.json"
    assert plan_path.is_file()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["inputs"]["tool_sources"] == []
    assert plan["inputs"]["config"]["path"] == "shipgate.yaml"


def test_declared_paths_cover_every_documented_shape() -> None:
    """Bare strings, ``{path: ...}`` objects, scalars, and nested blocks."""

    raw = {
        "agent": {"sdk": {"entrypoint": "agents/root.py"}},
        "tool_sources": [{"id": "mcp", "type": "mcp", "path": "mcp/tools.json"}],
        "openai_api": {
            "prompt_files": ["prompts/a.md", {"path": "prompts/b.md"}],
            "model_config": "openai-config.json",
            "function_schemas": [{"path": "schemas/fn.json", "name": "fn"}],
            "tools": ["tools/openai.json"],
        },
        "anthropic": {"prompt_files": ["prompts/c.md"]},
        "n8n": {"workflows": [{"path": "flows/", "optional": True}]},
        "codex_plugins": {
            "mcp_tool_inventories": [
                {"plugin": "p", "server": "s", "path": "codex/inv.json"}
            ]
        },
        "validation": {
            "mode": "human_in_the_loop",
            "evidence": {"approval_traces": [{"path": "evidence/approvals.jsonl"}]},
        },
        "checks": {
            "policy_packs": [{"path": "policies/org.yaml", "id": "org"}],
            "ignore": [{"check_id": "SHIP-X", "reason": "documented"}],
        },
        # Outputs and never-read declarations stay out; see declared_paths.py.
        "output": {"directory": "agents-shipgate-reports"},
        "organization": {"audit": {"registry": "org/registry.json"}},
        "baseline": {"audit_log": "baseline-audit.jsonl"},
    }

    assert declared_manifest_input_paths(raw) == [
        "agents/root.py",
        "codex/inv.json",
        "evidence/approvals.jsonl",
        "flows/",
        "mcp/tools.json",
        "openai-config.json",
        "policies/org.yaml",
        "prompts/a.md",
        "prompts/b.md",
        "prompts/c.md",
        "schemas/fn.json",
        "tools/openai.json",
    ]


@pytest.mark.parametrize("raw", [None, [], "shipgate", 7, {}])
def test_declared_paths_tolerate_a_manifest_that_does_not_validate(raw: Any) -> None:
    """Request identity must still be constructible for a broken manifest."""

    assert declared_manifest_input_paths(raw) == []


def test_every_artifact_path_field_is_reachable_from_the_derived_key_set() -> None:
    """The derivation, not a hand-kept list, is what keeps this complete.

    ``DECLARED_INPUT_PATH_BLOCKS`` is read off the manifest models so a new
    artifact list — or a whole new framework block — is covered without editing
    ``declared_paths``. This asserts the reflection still reaches every
    ``ArtifactPathConfig``-typed field, which is what would break if the schema
    grew a wrapper type the walk does not unpack.
    """

    def visit(model: type, block: str, seen: set[type]) -> list[tuple[str, str]]:
        if model in seen:
            return []
        seen.add(model)
        fields: list[tuple[str, str]] = []
        for name, field in model.model_fields.items():
            annotation = field.annotation
            carries = any(
                isinstance(arg, type) and issubclass(arg, ArtifactPathConfig)
                for arg in (annotation, *get_args(annotation))
            )
            if carries:
                fields.append((block, name))
                continue
            for arg in (annotation, *get_args(annotation)):
                if (
                    isinstance(arg, type)
                    and hasattr(arg, "model_fields")
                    and not issubclass(arg, ArtifactPathConfig)
                ):
                    fields.extend(visit(arg, block, seen))
        return fields

    declared: list[tuple[str, str]] = []
    for block, field in AgentsShipgateManifest.model_fields.items():
        for arg in (field.annotation, *get_args(field.annotation)):
            if (
                isinstance(arg, type)
                and hasattr(arg, "model_fields")
                and not issubclass(arg, ArtifactPathConfig)
            ):
                declared.extend(visit(arg, block, set()))

    assert declared, "reflection found no artifact-path fields at all"
    missing = sorted(
        f"{block}.{name}"
        for block, name in declared
        if name not in DECLARED_INPUT_PATH_BLOCKS.get(block, frozenset())
    )
    assert missing == [], (
        "manifest fields declare adapter input paths that never reach "
        f"input_set_id: {missing}"
    )


def test_derived_blocks_cover_every_framework_that_names_paths() -> None:
    """A named canary for the blocks issue #299 called out by name."""

    assert {
        "agent",
        "anthropic",
        "checks",
        "codex_plugins",
        "crewai",
        "google_adk",
        "langchain",
        "n8n",
        "openai_api",
        "tool_sources",
        "validation",
    } <= set(DECLARED_INPUT_PATH_BLOCKS)
    assert "prompt_files" in DECLARED_INPUT_PATH_BLOCKS["openai_api"]
    assert "prompt_files" in DECLARED_INPUT_PATH_BLOCKS["anthropic"]
    # openai_api.model_config is aliased; both spellings load, so both resolve.
    assert {"model_config", "api_model_config"} <= DECLARED_INPUT_PATH_BLOCKS["openai_api"]
