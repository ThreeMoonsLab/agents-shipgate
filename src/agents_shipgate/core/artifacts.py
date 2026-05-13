from __future__ import annotations

from typing import Protocol, TypeVar

T = TypeVar("T")


class FrameworkArtifact(Protocol):
    warnings: list[str]


class SurfaceSummaryArtifact(FrameworkArtifact, Protocol):
    def surface_summary(self) -> object: ...


class ArtifactBag:
    """Typed accessor over per-scan adapter artifacts."""

    __slots__ = ("_by_type",)

    def __init__(self, by_type: dict[str, object] | None = None) -> None:
        self._by_type: dict[str, object] = dict(by_type or {})

    def set(self, source_type: str, artifact: object) -> None:
        self._by_type[source_type] = artifact

    def has(self, source_type: str) -> bool:
        return source_type in self._by_type

    def get(self, source_type: str, expected_type: type[T]) -> T | None:
        artifact = self._by_type.get(source_type)
        if artifact is None:
            return None
        if not isinstance(artifact, expected_type):
            raise TypeError(
                f"Adapter for {source_type!r} returned "
                f"{type(artifact).__name__}, "
                f"expected {expected_type.__name__}"
            )
        return artifact

    def raw(self) -> dict[str, object]:
        return dict(self._by_type)
