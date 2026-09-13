"""Vendor each #659 case's two file versions for offline replay.

The live run fetches public repositories; CI cannot, deterministically. So each
case keeps the selected file at its base (the merge commit's first parent) and
its head (the merge commit), redacted, beside the links it came from.
`tests/test_host_config_replay.py` rebuilds a two-commit repository from them and
runs the comparison with no network.

Redaction is applied identically to both sides. JSON files follow
`benchmark/cold-start/vendor.py` exactly: every key is kept, values under `env`
and `headers` and any value whose key names a secret become `<redacted>`. TOML
and YAML are redacted line by line: a literal value of eight or more characters
whose key names a secret becomes `<redacted>`, unless it is a reference
(`${{ … }}`, `${…}`) or a permission level. Machine-local absolute paths are
masked everywhere. A change that lived only in a redacted value is invisible to
the replay; the case records that as `redacted_only_change`.

    python benchmark/host-config/vendor.py <runs.json> benchmark/host-config/cases
"""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path) -> ModuleType:
    # Unique module names: the cold-start harness imports its own `expected`.
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


expectation = _load("host_config_expected", HERE / "expected.py")
cold_vendor = _load("cold_start_vendor", HERE.parent / "cold-start" / "vendor.py")

_ASSIGNMENT = re.compile(r'^(\s*-?\s*"?)([A-Za-z0-9_.\-]+)("?\s*[:=]\s*)(.*?)(\s*)$')
_REFERENCE = re.compile(r"^[\"']?\$\{")
_LEVELS = {"read", "write", "none", "true", "false"}


def _redact_lines(text: str) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        match = _ASSIGNMENT.match(body)
        if match and cold_vendor.SECRET_KEY.search(match.group(2)):
            # An inline comment is not part of the value: `id-token: write  # for OIDC`
            # is the permission level `write`, and redacting it changed the workflow.
            raw = re.split(r"\s+#", match.group(4), maxsplit=1)[0].strip()
            value = raw.strip("\"'")
            if len(value) >= 8 and not _REFERENCE.match(raw) and value.lower() not in _LEVELS:
                body = f'{match.group(1)}{match.group(2)}{match.group(3)}"{cold_vendor.REDACTED}"'
        lines.append(cold_vendor.mask_local_paths(body) + ending)
    return "".join(lines)


def vendored_text(kind: str, text: str | None) -> str | None:
    if text is None:
        return None
    if kind.endswith(".json"):
        return cold_vendor.vendored_text(text)
    return _redact_lines(text)


def fetch(repo: str, sha: str, path: str) -> str | None:
    """The file at one commit, or ``None`` when it does not exist there."""

    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/contents/{path}?ref={sha}", "--jq", ".content"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        if "404" in result.stderr or "Not Found" in result.stderr:
            return None
        raise RuntimeError(f"{repo}@{sha}:{path}: {result.stderr.strip()[:200]}")
    return base64.b64decode(result.stdout).decode("utf-8")


def main(runs_path: Path, cases: Path) -> int:
    runs = json.loads(runs_path.read_text(encoding="utf-8"))
    for record in runs["records"]:
        case_dir = cases / record["case"]
        case_dir.mkdir(parents=True, exist_ok=True)
        shas = {"base": record["base_sha"], "head": record["merge_commit_sha"]}
        live = {side: fetch(record["repo"], sha, record["path"]) for side, sha in shas.items()}
        sides = {side: vendored_text(record["kind"], text) for side, text in live.items()}
        for side, text in sides.items():
            target = case_dir / f"{side}.txt"
            if text is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(text.encode("utf-8"))
        vendored = expectation.expected(record["kind"], sides["base"], sides["head"])
        (case_dir / "case.json").write_text(
            json.dumps(
                {
                    "repo": record["repo"],
                    "pr_number": record["pr_number"],
                    "kind": record["kind"],
                    "path": record["path"],
                    "base_sha": shas["base"],
                    "head_sha": shas["head"],
                    "coding_agent_marked": record.get("coding_agent_marked"),
                    "base_source": f"https://github.com/{record['repo']}/blob/{shas['base']}/{record['path']}",
                    "head_source": f"https://github.com/{record['repo']}/blob/{shas['head']}/{record['path']}",
                    "redacted_only_change": bool(record.get("expectation"))
                    and record["expectation"] != vendored,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"vendored {len(runs['records'])} cases into {cases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
