# Agents Shipgate maintenance and support

This record separates the responsibilities already documented in the repository
from the commitments that must be accepted before the first 1.x release.
[#494](https://github.com/ThreeMoonsLab/agents-shipgate/issues/494) retains the
owner confirmations, support-policy acceptance and recovery exercise;
[#572](https://github.com/ThreeMoonsLab/agents-shipgate/issues/572) retains the
release decision. Publishing this document supplies none of those confirmations.

## Responsibilities

Status checked against the repository on **2026-09-09**. A documented policy
owner is not evidence of access to a mailbox, a signing service or a recovery
account. Roles may share a person only where the existing independence rules
permit it.

| Responsibility | Documented owner or outstanding confirmation | Evidence and obligation |
| --- | --- | --- |
| Release/tag operation | Pengfei Hu (`pengfei-threemoonslab`) | Existing [release cadence](docs/release-runbook.md#cadence) names this role. The final candidate still needs its qualification, rehearsal and publication controls. |
| Product/security release-policy decisions | Pengfei Hu (`pengfei-threemoonslab`) | Existing [approved policy](docs/release-evidence-policy-decision.md) names this role. This does not assign security-inbox coverage or independent review. |
| Recording the 1.0 go/no-go and accepting the support commitment below | Confirmation outstanding | The release owner records the decision and its evidence in #494/#572; no acceptance is inferred from this table. |
| Beta sourcing, two blind human primary-label disciplines and independent adjudication | Confirmed assignments outstanding | [#512](https://github.com/ThreeMoonsLab/agents-shipgate/issues/512) records the actual people, conflicts, blindness and handoff. The pre-1.0 agent-labeling protocol cannot supply these duties. |
| Independent qualification signing/promotion | Confirmed owner and exact signing identity outstanding | [#509](https://github.com/ThreeMoonsLab/agents-shipgate/issues/509) binds this responsibility to the reviewed trust root and actual signed evidence. |
| Independent publication review and restricted release writers | Confirmed eligible reviewers/writers outstanding | [#573](https://github.com/ThreeMoonsLab/agents-shipgate/issues/573) verifies effective controls and independence from the initiator. A second login or an agent-created GitHub review proves neither. |
| Security intake, ordinary support and supported-version fixes | Coverage/capacity acceptance outstanding | The current contact is in [SECURITY.md](SECURITY.md). Mailbox custody, availability and a duty holder must be confirmed; no backup is documented as accepted here. |
| Account, signing and release-access recovery | Confirmed custodian and available recovery path outstanding | The [runbook inventory and tabletop](docs/release-runbook.md#operational-ownership-and-access-recovery) identify what the responsible people must verify. |

The owner records dated acceptance and a non-secret reference to the actual
access/independence evidence in #494 or a linked review. Private contact and
recovery details stay private; publish enough role/evidence information to
establish the obligation without publishing credentials. An unfilled required
role remains a release dependency, even when the engineering checks pass.

## Support by release line

**Current:** the product is pre-1.0. The latest pre-1.0 minor receives best-effort
security fixes under [SECURITY.md](SECURITY.md). Unqualified previews and source
checkouts are development channels, not supported stable releases.

**Proposed 1.x policy — owner acceptance pending in #494.** This is the concrete
policy to accept before publication, not a claim that a 1.x release or staffed
support service already exists:

| User's version | Fix and upgrade path |
| --- | --- |
| Latest published 1.x minor | Best-effort security and correctness fixes in patch releases, subject to the frozen compatibility contract. Use its latest patch when reporting or validating a fix. |
| Older 1.x minor | Upgrade to the latest supported 1.x minor. No automatic backports or maintenance horizon for older minors are promised. |
| Pre-1.0 after 1.x becomes supported | Follow the published migration route to 1.x; continued 0.x maintenance would need a separately accepted commitment. |
| Preview, RC or source checkout | Report the exact version/commit and channel. Availability does not imply release qualification or a stable support promise. |

This proposal adds no LTS term, paid SLA or fixed remediation deadline.
[STABILITY.md](STABILITY.md) governs compatibility, published schema URLs and
deprecation; [#569](https://github.com/ThreeMoonsLab/agents-shipgate/issues/569)
must establish the actual 1.x freeze and migration before this policy takes
effect. A patch must not silently reinterpret old evidence or turn an old
receipt into current authority. If a fix cannot respect the applicable contract,
publish an explicit compatibility/migration decision under those rules.

## Getting help and reporting a problem

Use the [existing issue tracker](https://github.com/ThreeMoonsLab/agents-shipgate/issues)
for ordinary bugs, installation failures and usage questions. Include the
version, install/channel information, supported platform, exact command and a
small redacted reproduction. Remove credentials and private repository data
from logs or reports before sharing them.

Suspected vulnerabilities go to the private contact in [SECURITY.md](SECURITY.md).
Acknowledgment and remediation are different obligations. The existing
three-business-day acknowledgment commitment still needs an accepted capacity
and absence arrangement for 1.x; this document neither invents coverage nor
silently replaces the existing commitment with a weaker one.

No alternate security intake owner or backup recovery custodian is confirmed in
this record. If the contact is unavailable, follow up through the same private
address; a public contact-availability issue must omit all vulnerability details.
Lack of a reply is not a successful handoff. The owner must resolve coverage or
explicitly revise the commitment before 1.x, and an unavailable required signer
or publication reviewer holds the release.

## Contributor and operator entry points

- [Local setup, dependency locks and sample goldens](CONTRIBUTING.md) provide
  the existing reproducible build/test recipes.
- [Accepted decisions](docs/decisions.md) and [STABILITY.md](STABILITY.md) own
  contract and compatibility decisions.
- [Release runbook](docs/release-runbook.md) owns qualification, publication and
  recovery; use its existing transaction and evidence requirements.
- [Operational ownership and access recovery](docs/release-runbook.md#operational-ownership-and-access-recovery)
  supplies the non-publishing tabletop procedure. Its results must come from
  the responsible people; no successful exercise is recorded here.
