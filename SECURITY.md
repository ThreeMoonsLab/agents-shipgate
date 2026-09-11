# Security Policy

Agents Shipgate is security-adjacent release tooling, so vulnerability reports are welcome.

## Reporting

Please do not open public issues for suspected vulnerabilities. Email `security@threemoonslab.com` with:

- affected version or commit;
- reproduction steps;
- impact;
- whether the issue affects local scanning, report output, GitHub Actions usage, or package distribution.

We will acknowledge reports within 3 business days.

This is an acknowledgment commitment, not a deadline for remediation.
The [maintenance record](MAINTAINERS.md#getting-help-and-reporting-a-problem)
identifies the outstanding 1.x capacity and absence-coverage confirmation.
No alternate intake owner is confirmed in that record. Follow up at the same
private address if a report has not been acknowledged; keep vulnerability
details out of public contact-availability requests. An unanswered message is
not a completed security handoff.

## Supported Versions

Pre-1.0 releases receive best-effort security fixes on the latest minor version.

The [proposed 1.x support policy](MAINTAINERS.md#support-by-release-line) names
the latest supported minor, patch-fix and older-version upgrade paths. It needs
the owner's acceptance before 1.x publication; current pre-1.0 support remains
as stated above. Stable compatibility and migration are governed by
[STABILITY.md](STABILITY.md).

## Scope

In scope:

- unexpected code execution;
- network or filesystem access that violates the documented trust model;
- unsafe parsing of manifests, OpenAPI files, MCP exports, or SDK source files;
- report output that leaks secrets beyond the provided inputs.

Out of scope:

- findings quality disagreements without a security consequence;
- vulnerabilities in downstream user tools scanned by Agents Shipgate;
- social engineering and denial-of-service against maintainers.
