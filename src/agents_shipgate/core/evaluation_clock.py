"""Run-scoped deterministic date used by verification policy evaluation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

_EVALUATION_DATE: ContextVar[date | None] = ContextVar(
    "agents_shipgate_evaluation_date", default=None
)


def evaluation_date() -> date:
    return _EVALUATION_DATE.get() or date.today()


@contextmanager
def use_evaluation_date(value: date) -> Iterator[None]:
    token = _EVALUATION_DATE.set(value)
    try:
        yield
    finally:
        _EVALUATION_DATE.reset(token)


__all__ = ["evaluation_date", "use_evaluation_date"]
