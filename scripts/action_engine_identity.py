"""Log the installed engine's existing content identity for version-based Actions.

Run by script path so the PR workspace cannot impersonate agents_shipgate.
The digest is the verifier's package-content identity, not a wheel ZIP hash.
The line is a diagnostic: an engine that cannot compute it (every release
before 1.0.0 predates this identity) gets a warning, never a failed install.
"""


def main() -> None:
    try:
        from agents_shipgate.core.verification_identity import _engine_distribution_sha256

        digest = _engine_distribution_sha256()
    except Exception as exc:  # noqa: BLE001 - diagnostic only; never fail the install
        print(f"::warning::installed engine identity unavailable ({type(exc).__name__}: {exc})")
        return
    print(f"verification_identity.engine_distribution_sha256={digest}")


if __name__ == "__main__":
    main()
