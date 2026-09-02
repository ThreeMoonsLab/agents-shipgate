"""Cut C rater tooling for the pre-1.0 safety-qualification corpus (issue #456).

Two maintainer tools live here:

- :mod:`build_packet` turns one case into the exact three-input packet that
  Amendment 1 condition 2 allows a rater session to see;
- :mod:`run_rater` runs one fresh, read-only, network-less agent session over
  a packet and archives its complete transcript content-addressed, producing
  one ``IndependentHumanLabelV1``.

Like ``benchmark/miner``, this is benchmark tooling: it uses ``git`` and
subprocesses, is not part of the wheel, and adds no CLI surface.
"""
