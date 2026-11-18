#!/usr/bin/env bash
# Deterministic baseline for a workspace with an exported tool list.
set -euo pipefail
task_python="${AGENTS_SHIPGATE_TASK_PYTHON:-python}"

"$task_python" -m agents_shipgate init --workspace . --minimal --write

"$task_python" - <<'PYTHON'
from pathlib import Path

import yaml

path = Path("shipgate.yaml")
manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
manifest["agent"]["name"] = "support-agent"
manifest["agent"]["declared_purpose"] = ["Look up support cases and respond to customers."]
manifest["tool_sources"] = [{"id": "support-tools", "type": "mcp", "path": "tools.json"}]
path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
PYTHON

"$task_python" -m agents_shipgate scan -c shipgate.yaml --ci-mode advisory
