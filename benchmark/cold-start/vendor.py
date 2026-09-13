"""Vendor each cold-start case's two file versions for offline replay (#660).

The live run clones public repositories; CI cannot, deterministically. So each
case keeps the selected file at its pinned base and head, redacted, beside the
link it came from. `tests/test_cold_start_replay.py` rebuilds a two-commit
repository from them and runs the comparison with no network.

Redaction is applied identically to both sides and keeps every key: values
under `env` and `headers`, and any value whose key names a secret, become
`<redacted>`. Machine-local absolute paths that third-party files carry — a home
directory (`/Users/<name>/`, `/home/<name>/`) or a temporary directory
(`/private/tmp/…`) — are masked in every string, because they name a person's
machine rather than a capability. A change that lived only in such a value is therefore invisible
to the replay, which is recorded in the case as `redacted_only_change` when the
live expectation and the vendored one disagree.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import expected as expectation  # noqa: E402

SECRET_KEY = re.compile(r"(token|secret|password|passwd|api[_-]?key|credential|auth)", re.IGNORECASE)
REDACTED = "<redacted>"
_LOCAL_PATHS = (
    (re.compile(r"/private/tmp/[^\s\"')]*"), "<tmp-path>"),
    (re.compile(r"/(Users|home)/[^/\s\"')]+/"), r"/\1/<user>/"),
)


def mask_local_paths(text: str) -> str:
    """Mask machine-local absolute paths; applied identically wherever a case is recorded."""

    for pattern, replacement in _LOCAL_PATHS:
        text = pattern.sub(replacement, text)
    return text


def redact(value, *, parent: str = ""):
    if isinstance(value, dict):
        return {
            key: (
                {k: REDACTED for k in item} if key in {"env", "headers"} and isinstance(item, dict)
                else REDACTED if isinstance(item, str) and SECRET_KEY.search(key)
                else redact(item, parent=key)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, parent=parent) for item in value]
    if isinstance(value, str):
        return mask_local_paths(value)
    return value


def vendored_text(text: str | None) -> str | None:
    if text is None:
        return None
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return mask_local_paths(text)  # an unparsed side is the case; keep its bytes otherwise
    return json.dumps(redact(document), indent=2, sort_keys=True) + "\n"


def main(runs_path: Path, clones: Path, cases: Path) -> int:
    runs = json.loads(runs_path.read_text())
    for record in runs["records"]:
        clone = clones / record["case"]
        case_dir = cases / record["case"]
        case_dir.mkdir(parents=True, exist_ok=True)
        sides = {}
        for side in ("base", "head"):
            sha = record[f"{side}_sha"]
            result = subprocess.run(["git", "-C", str(clone), "show", f"{sha}:{record['kind']}"], capture_output=True, text=True)
            sides[side] = vendored_text(result.stdout) if result.returncode == 0 else None
            target = case_dir / f"{side}.txt"
            if sides[side] is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(sides[side])
        live = record.get("expectation")
        vendored = expectation.expected(record["kind"], sides["base"], sides["head"])
        (case_dir / "case.json").write_text(json.dumps({
            "repo": record["repo"], "kind": record["kind"],
            "base_sha": record["base_sha"], "head_sha": record["head_sha"],
            "base_source": f"https://github.com/{record['repo']}/blob/{record['base_sha']}/{record['kind']}",
            "head_source": f"https://github.com/{record['repo']}/blob/{record['head_sha']}/{record['kind']}",
            "redacted_only_change": bool(live) and live != vendored,
        }, indent=2) + "\n")
    print(f"vendored {len(runs['records'])} cases into {cases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*(Path(arg) for arg in sys.argv[1:4])))
