"""Who approved a pull request, read from GitHub's reviews (#1118).

The attestation records who *merged* a change (``org.actor``) and the
declared ``human_ack`` state. Neither is an approval by a second person. GitHub
holds that fact as the pull request's reviews; this module reads the JSON array
``GET /repos/{owner}/{repo}/pulls/{number}/reviews`` returns, which the
workflow fetches. The engine never calls the API.

Pure and deterministic: the same reviews, repository, pull request, author and
head commit always give the same evaluation. It never decides whether a change
may merge; ``release_decision.decision`` is the only gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_shipgate.schemas.attestation import (
    ApprovalSource,
    ApprovalStatus,
    IgnoredApprovalReason,
    approval_status_for,
)

APPROVAL_SOURCE: ApprovalSource = "github_pull_request_reviews"

# GitHub review states. ``COMMENTED`` and ``PENDING`` never change a
# reviewer's standing; ``DISMISSED`` clears it.
_DECISIVE_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})
_KNOWN_STATES = _DECISIVE_STATES | {"COMMENTED", "PENDING"}


class ReviewsError(ValueError):
    """The reviews file cannot be evaluated for this pull request."""


@dataclass(frozen=True)
class Approver:
    login: str
    review_id: int
    submitted_at: str
    commit_id: str
    on_head: bool


@dataclass(frozen=True)
class IgnoredApproval:
    login: str
    review_id: int
    reason: IgnoredApprovalReason


@dataclass(frozen=True)
class ApprovalEvaluation:
    status: ApprovalStatus
    pr_number: str
    pr_author: str
    head_sha: str
    approvers: tuple[Approver, ...]
    changes_requested_by: tuple[str, ...]
    ignored_approvals: tuple[IgnoredApproval, ...]


def evaluate_pull_request_reviews(
    reviews: Any,
    *,
    repository: str,
    pr_number: str,
    pr_author: str,
    head_sha: str,
) -> ApprovalEvaluation:
    """Evaluate GitHub reviews of one pull request against its attested head.

    A reviewer's standing is their latest ``APPROVED``, ``CHANGES_REQUESTED``
    or ``DISMISSED`` review, ordered by ``submitted_at`` and then review id;
    comments do not change it. The pull request's author and bot accounts
    never count as approvers, and an approval they submitted is recorded under
    ``ignored_approvals``. Any standing change request makes the status
    ``changes_requested``, as GitHub's own review decision does.

    Raises :class:`ReviewsError` when the input is not a reviews array of this
    pull request: a review that names another pull request (or repository)
    is refused, never skipped, so a file cannot be borrowed from elsewhere.
    """

    if not isinstance(reviews, list):
        raise ReviewsError("the reviews file must be the JSON array the reviews API returns")
    expected_suffix = f"/repos/{repository}/pulls/{pr_number}".lower()
    author = pr_author.lower()
    head = head_sha.lower()

    standing: dict[str, tuple[str, dict[str, Any]]] = {}
    ignored: list[IgnoredApproval] = []
    parsed = [_review(item, index, expected_suffix) for index, item in enumerate(reviews)]
    for review in sorted(parsed, key=lambda item: (item["submitted_at"], item["id"])):
        state = review["state"]
        if state not in _DECISIVE_STATES:
            continue
        login = review["login"]
        if review["is_bot"] or login.lower() == author:
            if state == "APPROVED":
                ignored.append(
                    IgnoredApproval(
                        login=login,
                        review_id=review["id"],
                        reason="bot" if review["is_bot"] else "pr_author",
                    )
                )
            continue
        standing[login] = (state, review)

    approvers = tuple(
        sorted(
            (
                Approver(
                    login=login,
                    review_id=review["id"],
                    submitted_at=review["submitted_at"],
                    commit_id=review["commit_id"],
                    on_head=review["commit_id"].lower() == head,
                )
                for login, (state, review) in standing.items()
                if state == "APPROVED"
            ),
            key=lambda item: (item.login.lower(), item.review_id),
        )
    )
    changes_requested_by = tuple(
        sorted(
            (login for login, (state, _) in standing.items() if state == "CHANGES_REQUESTED"),
            key=str.lower,
        )
    )
    return ApprovalEvaluation(
        status=approval_status_for(
            (item.on_head for item in approvers), changes_requested_by
        ),
        pr_number=pr_number,
        pr_author=pr_author,
        head_sha=head_sha,
        approvers=approvers,
        changes_requested_by=changes_requested_by,
        ignored_approvals=tuple(
            sorted(ignored, key=lambda item: (item.login.lower(), item.review_id))
        ),
    )


def _review(item: Any, index: int, expected_suffix: str) -> dict[str, Any]:
    where = f"review {index}"
    if not isinstance(item, dict):
        raise ReviewsError(f"{where} is not an object")
    review_id = item.get("id")
    if type(review_id) is not int:
        raise ReviewsError(f"{where} has no integer id")
    state = item.get("state")
    if state not in _KNOWN_STATES:
        raise ReviewsError(f"{where} has an unknown state {state!r}")
    url = item.get("pull_request_url")
    if not isinstance(url, str) or not url.lower().endswith(expected_suffix):
        raise ReviewsError(
            f"{where} (id {review_id}) belongs to another pull request or repository: "
            f"{url!r} does not end with {expected_suffix!r}"
        )
    user = item.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    if not isinstance(login, str) or not login.strip():
        # GitHub returns ``user: null`` for a deleted account. It cannot be
        # attributed to a person, so it is not an approval; it is not an
        # error either.
        login = None
    submitted_at = item.get("submitted_at")
    commit_id = item.get("commit_id")
    if state in _DECISIVE_STATES and login is not None:
        if not isinstance(submitted_at, str) or not submitted_at:
            raise ReviewsError(f"{where} (id {review_id}) has no submitted_at")
        if not isinstance(commit_id, str) or not commit_id:
            raise ReviewsError(f"{where} (id {review_id}) has no commit_id")
    is_bot = login is not None and (
        login.lower().endswith("[bot]")
        or (isinstance(user, dict) and user.get("type") == "Bot")
    )
    return {
        "id": review_id,
        # A review with no attributable user is treated like a comment.
        "state": state if login is not None else "COMMENTED",
        "login": login or "",
        "submitted_at": submitted_at if isinstance(submitted_at, str) else "",
        "commit_id": commit_id if isinstance(commit_id, str) else "",
        "is_bot": is_bot,
    }


__all__ = [
    "APPROVAL_SOURCE",
    "ApprovalEvaluation",
    "ApprovalStatus",
    "Approver",
    "IgnoredApproval",
    "IgnoredApprovalReason",
    "ReviewsError",
    "evaluate_pull_request_reviews",
]
