"""Plain data types shared by every layer. No database access and no printing here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Status(str, Enum):
    SAVED = "saved"
    APPLIED = "applied"
    SCREENING = "screening"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"

    def __str__(self) -> str:
        return self.value


# Display order everywhere a list of statuses is shown.
ALL_STATUSES: tuple[Status, ...] = tuple(Status)

# Closed applications are finished: no further moves, and they drop out of the follow-up list.
CLOSED_STATUSES = frozenset({Status.REJECTED, Status.WITHDRAWN})

# Statuses that mean the employer answered. Withdrawing is the user's own action, not a response.
RESPONSE_STATUSES = frozenset(
    {Status.SCREENING, Status.INTERVIEW, Status.OFFER, Status.REJECTED}
)


@dataclass(frozen=True)
class NewApplication:
    """Everything needed to create an application. `id` and timestamps are assigned on insert."""

    company: str
    role: str
    link: str | None = None
    location: str | None = None
    source: str | None = None
    notes: str | None = None
    status: Status = Status.APPLIED
    date_applied: date | None = None
    follow_up_date: date | None = None
    first_response_date: date | None = None


@dataclass(frozen=True)
class Application:
    id: int
    company: str
    role: str
    link: str | None
    location: str | None
    source: str | None
    notes: str | None
    status: Status
    date_applied: date | None
    follow_up_date: date | None
    first_response_date: date | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StatusChange:
    id: int
    application_id: int
    old_status: Status | None
    new_status: Status
    changed_on: date
    note: str | None


@dataclass(frozen=True)
class DueItem:
    """An application on the follow-up list.

    `days_overdue` is positive when the follow-up date has passed, 0 when it is
    today and negative when it is still ahead.
    """

    application: Application
    days_overdue: int


@dataclass(frozen=True)
class Summary:
    """Numbers computed from the stored applications only. See README for the definitions."""

    total: int
    by_status: dict[Status, int]
    applied: int
    responded: int
    # None when nothing has been applied yet, so there is nothing to divide by.
    response_rate: float | None
    # None when no responded application has both dates.
    avg_days_to_first_response: float | None
    avg_based_on: int
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True)
class ImportResult:
    rows_read: int
    imported: int
    skipped_duplicates: int
    dry_run: bool = False
