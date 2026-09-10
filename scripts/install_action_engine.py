#!/usr/bin/env python3
"""Install an explicitly supplied, hash-checked local candidate wheel (#570).

The Action's ordinary source/version paths are unchanged. This route captures
one local file privately, checks its digest, and installs those same bytes.
No candidate code is imported to validate its metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path


def install_wheel(*, workspace: Path, wheel: str, sha256: str, version: str = "") -> None:
    if version:
        raise ValueError("shipgate_wheel cannot be combined with shipgate_version")
    if not wheel or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("shipgate_wheel and its full lowercase shipgate_wheel_sha256 are required")
    root = workspace.resolve(strict=True)
    path = Path(wheel)
    path = path if path.is_absolute() else root / path
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("The candidate wheel must be inside GITHUB_WORKSPACE") from exc
    if ".." in relative.parts:
        raise ValueError("The candidate wheel path cannot contain parent traversal")
    cursor = root
    info = root.lstat()
    for part in relative.parts:
        cursor /= part
        info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("The candidate wheel path cannot traverse symlinks or reparse points")
    if not stat.S_ISREG(info.st_mode) or info.st_size > 128 * 1024 * 1024:
        raise ValueError("The candidate wheel must be a regular file of at most 128 MiB")
    match = re.fullmatch(r"agents_shipgate-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl", path.name)
    if not match:
        raise ValueError("Expected an agents_shipgate X.Y.Z py3-none-any wheel filename")
    with tempfile.TemporaryDirectory(prefix="shipgate-action-wheel-") as directory:
        snapshot = Path(directory) / path.name
        # O_BINARY matters on Windows runners: without it os.open() hands back
        # a text-mode descriptor, and a zip read through one is rewritten at
        # every CRLF and truncated at the first 0x1A byte — so a wheel that is
        # exactly right never captures. The stdlib ORs it for the same reason
        # (`_pyio.FileIO`, `tempfile._bin_openflags`); it is 0 elsewhere.
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_BINARY", 0),
        )
        with os.fdopen(descriptor, "rb") as source, snapshot.open("wb") as destination:
            current = os.fstat(source.fileno())
            if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino, current.st_size) != (
                info.st_dev, info.st_ino, info.st_size
            ):
                raise ValueError("The candidate wheel changed before capture")
            remaining = info.st_size + 1
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                destination.write(chunk)
                remaining -= len(chunk)
            if destination.tell() != info.st_size:
                raise ValueError("The candidate wheel size changed during capture")
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != sha256:
            raise ValueError("Candidate wheel SHA-256 mismatch; nothing was installed")
        with zipfile.ZipFile(snapshot) as archive:
            names = archive.namelist()
            expected = f"agents_shipgate-{match[1]}.dist-info/METADATA"
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            if metadata_names != [expected] or len(names) != len(set(names)):
                raise ValueError("Candidate wheel must have one matching distribution METADATA")
            if archive.getinfo(expected).file_size > 1024 * 1024:
                raise ValueError("Candidate wheel METADATA exceeds 1 MiB")
            with archive.open(expected) as member:
                raw_metadata = member.read(1024 * 1024 + 1)
            if len(raw_metadata) > 1024 * 1024:
                raise ValueError("Candidate wheel METADATA exceeds 1 MiB")
            metadata = BytesParser().parsebytes(raw_metadata)
            if metadata.get_all("Name") != ["agents-shipgate"] or metadata.get_all("Version") != [
                match[1]
            ]:
                raise ValueError("Candidate wheel METADATA disagrees with its filename")
        # Reinstall even when the runner already has the same version: its
        # bytes may be a different development build of that version.
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(snapshot)],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        install_wheel(
            workspace=Path(os.environ["GITHUB_WORKSPACE"]),
            wheel=os.environ.get("SHIPGATE_WHEEL", ""),
            sha256=os.environ.get("SHIPGATE_WHEEL_SHA256", ""),
            version=os.environ.get("SHIPGATE_VERSION", ""),
        )
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        print(f"Candidate wheel install refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
