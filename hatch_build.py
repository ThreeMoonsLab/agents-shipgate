"""Opt-in, reproducible source identity for a final candidate wheel (#570).

This is provenance, never release qualification. Ordinary and preview builds
carry no record; a later build cannot inherit one from the source directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.builders.wheel import WheelBuilder

_ENV = "AGENTS_SHIPGATE_CANDIDATE_SOURCE_COMMIT"
_MEMBER = "agents_shipgate/_meta/release-source.json"


def candidate_source(root: Path, requested: str, package_version: str) -> dict[str, str]:
    """Refuse a guessed identity, modified source, or preview version."""
    if not re.fullmatch(r"[0-9a-f]{40}", requested):
        raise ValueError(f"{_ENV} must be a full lowercase 40-character commit SHA")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", package_version):
        raise ValueError("Candidate provenance requires a final X.Y.Z version, not a preview")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args], env=env, capture_output=True,
            text=True, check=True, timeout=30,
        )
        return result.stdout.strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != root.resolve():
        raise ValueError("Candidate build must run at its own repository root")
    if git("rev-parse", "HEAD") != requested:
        raise ValueError("Candidate source commit does not match checked-out HEAD")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Candidate source must be clean, including untracked files")
    return {
        "schema_version": "shipgate.release_source/v1",
        "source_commit": requested,
        "package_version": package_version,
    }


class CandidateSourceHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        self._candidate = None
        requested = os.environ.get(_ENV)
        if requested is None:
            return
        if self.target_name != "wheel" or version != "standard":
            raise ValueError("Candidate provenance is supported only for a standard wheel")
        record = candidate_source(Path(self.root), requested, self.metadata.version)
        self._payload = self._head_payload(requested)
        self._candidate = tempfile.TemporaryDirectory(prefix="shipgate-candidate-source-")
        path = Path(self._candidate.name) / "release-source.json"
        path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        build_data["force_include"][str(path)] = _MEMBER

    def finalize(self, version: str, build_data: dict, artifact_path: str) -> None:
        if self._candidate is None:
            return
        try:
            # Do not leave a candidate artifact behind if its source moved
            # while the backend was reading it.
            candidate_source(Path(self.root), os.environ[_ENV], self.metadata.version)
            if self._head_payload(os.environ[_ENV]) != self._payload:
                raise ValueError("Selected wheel inputs changed during the candidate build")
            # Validate the bytes the backend actually archived, not just the
            # before/after filesystem. A transient edit cannot be stamped as
            # HEAD merely because it was restored before finalize.
            with zipfile.ZipFile(artifact_path) as archive:
                for name, digest in self._payload.items():
                    if archive.namelist().count(name) != 1:
                        raise ValueError(f"Candidate payload member missing or duplicated: {name}")
                    if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                        raise ValueError(f"Candidate payload is not the committed input: {name}")
                for name in archive.namelist():
                    if name not in self._payload and name != _MEMBER and not name.startswith(
                        f"agents_shipgate-{self.metadata.version}.dist-info/"
                    ):
                        raise ValueError(f"Candidate contains an unbound payload member: {name}")
        except Exception:
            Path(artifact_path).unlink(missing_ok=True)
            raise
        finally:
            self._candidate.cleanup()

    def _head_payload(self, requested: str) -> dict[str, str]:
        """Use Hatch's own selection, including forced and ignored inputs.

        Git status can hide a file through info/exclude while Hatch packages
        it. Each selected file must be a regular tracked HEAD blob whose
        current bytes match the object identity; no ignore policy decides it.
        """
        root = Path(self.root).resolve()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        tree = subprocess.check_output(
            ["git", "-C", str(root), "ls-tree", "-r", "-z", requested], env=env, timeout=30,
        )
        tracked = {}
        committed_bytes = {}
        for entry in tree.split(b"\0"):
            if not entry:
                continue
            header, path = entry.split(b"\t", 1)
            mode, kind, digest = header.decode("ascii").split()
            relative = os.fsdecode(path)
            tracked[relative] = (mode, kind, digest)
            # Metadata inputs (pyproject/readme/build hook) need the same
            # protection even when they are not copied as payload members.
            # Direct blob comparison also defeats assume-unchanged flags.
            if mode in {"100644", "100755"} and kind == "blob":
                candidate = root / relative
                if any(p.is_symlink() for p in (candidate, *candidate.parents) if p != root):
                    raise ValueError(f"Candidate input traverses a symlink: {relative}")
                raw = candidate.read_bytes()
                blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
                if blob != digest:
                    raise ValueError(f"Candidate input bytes differ from HEAD: {relative}")
                committed_bytes[relative] = hashlib.sha256(raw).hexdigest()
        builder = WheelBuilder(str(root), metadata=self.metadata)
        payload = {}
        for selected in builder.recurse_included_files():
            path = Path(selected.path)
            try:
                relative = path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Candidate input is outside the committed root: {path}") from exc
            entry = tracked.get(relative.as_posix())
            if entry is None or entry[0] not in {"100644", "100755"} or entry[1] != "blob":
                raise ValueError(f"Candidate input is not a regular tracked HEAD file: {relative}")
            if any(p.is_symlink() for p in (path, *path.parents) if p != root):
                raise ValueError(f"Candidate input traverses a symlink: {relative}")
            payload[selected.distribution_path] = committed_bytes[relative.as_posix()]
        return payload
