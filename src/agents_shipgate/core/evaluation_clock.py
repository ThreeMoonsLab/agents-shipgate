"""Evaluation and trust-decay clocks used by static verification.

``evaluation_date`` is content-bound provenance.  A verifier may derive it
from the evaluated commit so deterministic, non-security-sensitive evaluation
can be reproduced.

Hard expiry is different: a commit author controls commit timestamps and must
never be able to extend a human acknowledgement, severity override, or
baseline exception.  ``trust_expiry_date`` therefore uses the later of the
content-bound evaluation date and the verifier's wall-clock date.  This split
is intentional and security-sensitive.
"""

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


def trust_expiry_date() -> date:
    """Return the conservative date for reviewer-owned trust expiry.

    The value can never be earlier than the verifier's wall clock.  A
    backdated commit therefore cannot keep an expired trust grant active.  A
    future evaluation date may expire a grant early, which is conservative.
    """

    return max(evaluation_date(), date.today())


@contextmanager
def use_evaluation_date(value: date) -> Iterator[None]:
    token = _EVALUATION_DATE.set(value)
    try:
        yield
    finally:
        _EVALUATION_DATE.reset(token)


__all__ = ["evaluation_date", "trust_expiry_date", "use_evaluation_date"]
