"""Reviewer-lens computation.

This package holds the *computation* of the three reviewer lenses
that the report layer renders:

- :mod:`tool_surface` — tool inventory snapshot + base/head diff
  (formerly ``report/tool_surface_diff.py``).
- :mod:`capability_intent` — observed-capability vs. declared-intent
  diff plus suggested release scenarios (formerly
  ``report/capability_diff.py``).
- :mod:`action_surface` — action snapshot + base/head diff plus
  built-in and user-declared action-surface policies that emit
  blocking findings (formerly ``report/action_surface_diff.py``).

Rendering of these lenses lives in :mod:`agents_shipgate.report`
(markdown / json / sarif) and :mod:`agents_shipgate.packet`. The split
is deliberate: keeping computation in ``core/`` prevents domain logic
from accreting into the formatter layer and keeps the formatter free
to focus on output shape.

External callers should import from this package (or its submodules)
rather than from ``report/``.
"""
