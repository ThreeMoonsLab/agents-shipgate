#!/usr/bin/env bash
# Deterministic baseline for task 01. The pytest harness runs this script to
# verify the task is well-formed (independent of any real agent). It performs
# the steps described in prompt.md.
set -euo pipefail

# 1. (Skip install — assume `agents-shipgate` is already on PATH in the test env.)

# 2. Generate the starter manifest.
agents-shipgate init --workspace . --write

# 3. Replace CHANGE_ME placeholders. The starter template lists the workspace
#    name as the project; we fill in the agent name and purpose based on
#    tools.json.
python - <<'PY'
from pathlib import Path
text = Path("shipgate.yaml").read_text()
text = text.replace("name: CHANGE_ME", "name: support-agent")
text = text.replace("    - CHANGE_ME", "    - look up support cases and respond to customers")
Path("shipgate.yaml").write_text(text)
PY

# 4. Run the scan.
agents-shipgate scan -c shipgate.yaml --ci-mode advisory
