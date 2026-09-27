"""Log the installed engine's existing content identity for version-based Actions.

Run by script path so the PR workspace cannot impersonate agents_shipgate.
The digest is the verifier's package-content identity, not a wheel ZIP hash.
"""
from agents_shipgate.core.verification_identity import _engine_distribution_sha256


def main() -> None:
    print(f"verification_identity.engine_distribution_sha256={_engine_distribution_sha256()}")


if __name__ == "__main__":
    main()
