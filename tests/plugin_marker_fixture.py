"""An installed third-party adapter plugin that records when it is imported.

The plugin opt-in (``AGENTS_SHIPGATE_ENABLE_PLUGINS``) is a trust boundary:
plugins are Python code, and importing one runs it. Monkeypatching
``entry_points`` would only test the gate's own branch, so this installs a real
distribution instead — a ``*.dist-info`` directory declaring an
``agents_shipgate.adapters`` entry point, on ``sys.path`` — and discovery finds
it the way it would find a ``pip install``. The evidence that plugin code ran
is a file its module writes at import time (#1020).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest


def install_marker_adapter_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install the plugin for this test, with the opt-in unset.

    Returns the path the plugin writes when it is imported. The module name is
    unique per call, so an import left in ``sys.modules`` by one test can never
    stand in for another test's; the distribution leaves ``sys.path`` when the
    test ends.
    """

    token = uuid.uuid4().hex[:12]
    module = f"shipgate_marker_plugin_{token}"
    site = tmp_path / "marker-site"
    site.mkdir()
    marker = tmp_path / f"{module}.imported"
    (site / f"{module}.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
        "\n"
        "\n"
        "class Adapter:\n"
        f"    source_type = 'marker_{token}'\n"
        "    scope = 'per_source'\n"
        "\n"
        "    def load(self, source, base_dir, manifest):\n"
        "        return []\n",
        encoding="utf-8",
    )
    dist_info = site / f"{module}-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: shipgate-marker-plugin-{token}\nVersion: 1.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        f"[agents_shipgate.adapters]\nmarker = {module}:Adapter\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.delenv("AGENTS_SHIPGATE_ENABLE_PLUGINS", raising=False)
    return marker
