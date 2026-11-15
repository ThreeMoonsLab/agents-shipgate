# Security Policy

Agents Shipgate is security-adjacent release tooling, so vulnerability reports are welcome.

## Reporting

Please do not open public issues for suspected vulnerabilities. Report privately
through either route:

- **GitHub private vulnerability reporting:**
  [open a private report](https://github.com/ThreeMoonsLab/agents-shipgate/security/advisories/new).
  The form works only while private vulnerability reporting is turned on for
  the repository, which the
  [release runbook](docs/release-runbook.md#repository-security-controls)
  records as required repository state. If the form is unavailable, use email.
- **Email:** `security@threemoonslab.com`.

Include:

- affected version or commit;
- reproduction steps;
- impact;
- whether the issue affects local scanning, report output, GitHub Actions usage, or package distribution.

We will acknowledge reports within 3 business days.

This is an acknowledgment commitment, not a deadline for remediation.
The [maintenance record](MAINTAINERS.md#getting-help-and-reporting-a-problem)
identifies the outstanding 1.x capacity and absence-coverage confirmation.
No alternate intake owner is confirmed in that record. Follow up through the
same private route if a report has not been acknowledged; keep vulnerability
details out of public contact-availability requests. An unanswered message is
not a completed security handoff.

Published advisories are listed at
<https://github.com/ThreeMoonsLab/agents-shipgate/security/advisories>.

## Supported Versions

Latest published release: `v1.3.0`.

| Version | Fix and upgrade path |
| --- | --- |
| 1.3.x, the latest 1.x minor | Supported. Best-effort security and correctness fixes ship in 1.3 patch releases, under the compatibility contract in [STABILITY.md](STABILITY.md). Report against, and validate a fix on, the latest 1.3 patch. |
| 1.0.x, 1.1.x and 1.2.x, the older 1.x minors | Not supported. Upgrade to the latest 1.x minor; fixes are not backported. |
| 0.x | Not supported. Follow the published migration route to 1.x; no further 0.x maintenance is promised. |
| Previews, release candidates and source checkouts | Development channels with no stable support promise. Report the exact version or commit and the channel. |

This is the 1.x support policy in [MAINTAINERS.md](MAINTAINERS.md#support-by-release-line),
accepted on 2026-11-15. It adds no long-term-support term, paid SLA or fixed
remediation deadline. Stable compatibility and migration are governed by
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
