from pathlib import Path

import yaml


def test_github_script_reads_output_dir_from_env():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "OUTPUT_DIR: ${{ inputs.output_dir }}" in text
    assert "process.env.OUTPUT_DIR" in text
    assert 'path.join("${{ inputs.output_dir }}", "report.json")' not in text


def test_action_installs_from_source_when_no_pypi_version_is_set():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert 'default: ""' in text
    assert 'python -m pip install "${GITHUB_ACTION_PATH}"' in text
    assert 'agents-shipgate==${SHIPGATE_VERSION}' in text


def test_action_has_marketplace_metadata_and_outputs():
    data = yaml.safe_load(Path("action.yml").read_text(encoding="utf-8"))

    assert data["name"] == "Agents Shipgate"
    assert data["author"] == "ThreeMoonsLab"
    assert data["branding"] == {"icon": "shield", "color": "blue"}
    assert {
        "status",
        "critical_count",
        "high_count",
        "baseline_new_count",
        "report_json",
        "exit_code",
    } <= set(data["outputs"])


def test_action_preserves_reports_before_applying_exit_code():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "id: scan" in text
    assert "exit 0" in text
    assert "Apply Agents Shipgate exit code" in text
    assert "steps.scan.outputs.exit_code" in text
    assert "FAIL_ON: ${{ inputs.fail_on }}" in text
    assert "BASELINE: ${{ inputs.baseline }}" in text
    assert "NO_PLUGINS: ${{ inputs.no_plugins }}" in text
    assert "args+=(--no-plugins)" in text


def test_action_pr_comment_truncates_user_controlled_text():
    text = Path("action.yml").read_text(encoding="utf-8")

    assert "const truncate =" in text
    assert "truncate(finding.title || finding.check_id, 240)" in text
    assert "].join(\"\\n\"), 6000)" in text


def test_marketplace_action_repo_has_ci_and_release_workflows():
    workflow_dir = Path(".github/workflows")

    assert workflow_dir.exists()
    assert {path.name for path in workflow_dir.glob("*")} == {"ci.yml", "release.yml"}


def test_release_workflow_uses_release_security_steps():
    text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "uv publish --trusted-publishing always" in text
    assert "sigstore sign" in text
    assert "cyclonedx-py environment" in text
