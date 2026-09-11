from __future__ import annotations

import hashlib
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.install_action_engine import install_wheel


@pytest.fixture
def wheel(tmp_path: Path) -> Path:
    path = tmp_path / "agents_shipgate-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("agents_shipgate-1.0.0.dist-info/METADATA",
                         "Name: agents-shipgate\nVersion: 1.0.0\n")
    return path


def test_installer_pins_captured_bytes_even_if_original_is_replaced(
    wheel: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = wheel.read_bytes()
    installed = []

    def pip(argv: list[str], **kwargs: object) -> None:
        wheel.write_bytes(b"replacement")
        snapshot = Path(argv[-1])
        assert snapshot != wheel
        assert snapshot.read_bytes() == original
        assert snapshot.name == wheel.name
        assert "--force-reinstall" in argv and "--no-deps" in argv
        installed.append(snapshot)

    monkeypatch.setattr(subprocess, "run", pip)
    install_wheel(workspace=wheel.parent, wheel=wheel.name,
                  sha256=hashlib.sha256(original).hexdigest())
    assert len(installed) == 1
    assert not installed[0].exists()


@pytest.mark.parametrize("case", ["wrong-hash", "missing-hash", "missing-path", "version", "url"])
def test_invalid_selection_never_installs(
    wheel: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    def pip(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid wheel selection reached pip")
    monkeypatch.setattr(subprocess, "run", pip)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    kwargs = dict(workspace=wheel.parent, wheel=wheel.name, sha256=digest)
    if case == "wrong-hash":
        kwargs["sha256"] = "a" * 64
    elif case == "missing-hash":
        kwargs["sha256"] = ""
    elif case == "missing-path":
        kwargs["wheel"] = ""
    elif case == "version":
        kwargs["version"] = "1.0.0"
    else:
        kwargs["wheel"] = "https://example.invalid/candidate.whl"
    with pytest.raises((ValueError, OSError)):
        install_wheel(**kwargs)


def test_installer_rejects_symlink_and_outside_workspace(wheel: Path, tmp_path: Path) -> None:
    link = tmp_path / "linked.whl"
    try:
        link.symlink_to(wheel)
    except OSError:
        pytest.skip("symlinks unavailable")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="symlinks"):
        install_wheel(workspace=tmp_path, wheel=link.name, sha256=digest)
    nested = tmp_path / "nested"
    nested.mkdir()
    with pytest.raises(ValueError, match="inside"):
        install_wheel(workspace=nested, wheel=str(wheel), sha256=digest)


def test_hash_match_does_not_accept_conflicting_metadata(wheel: Path) -> None:
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("agents_shipgate-1.0.0.dist-info/METADATA",
                         "Name: agents-shipgate\nVersion: 0.1.0\n")
    with pytest.raises(ValueError, match="METADATA"):
        install_wheel(workspace=wheel.parent, wheel=wheel.name,
                      sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())


def test_compressed_oversized_metadata_is_refused_before_install(
    wheel: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("agents_shipgate-1.0.0.dist-info/METADATA", "x" * (1024 * 1024 + 1))
    assert wheel.stat().st_size < 10_000
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("pip ran"))
    with pytest.raises(ValueError, match="METADATA exceeds"):
        install_wheel(workspace=wheel.parent, wheel=wheel.name,
                      sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
