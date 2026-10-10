"""Service layer: the rules of the tracker.

It takes checked values (dates, Status), applies the rules, and stores the
result through the database layer. It never prints and never reads the clock:
"today" is passed in, so the same call gives the same answer in a test.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta

from jobtracker import db, status_rules
from jobtracker.errors import NotFoundError, ValidationError
from jobtracker.models import (
    CLOSED_STATUSES,
    RESPONSE_STATUSES,
    Application,
    DueItem,
    NewApplication,
    Status,
    StatusChange,
    Summary,
)
from jobtracker.validation import clean_optional, require_text

# Marks "leave this field alone" in edit(), because None already means "clear it".
UNSET = object()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TrackerService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- creating -----------------------------------------------------------

    def check_new(self, new: NewApplication, *, today: date) -> NewApplication:
        """Validate a new application and return it with its text fields trimmed.

        Used by add() and, row by row, by the CSV import.
        """
        company = require_text(new.company, field="company")
        role = require_text(new.role, field="role")
        status, applied, first_response = new.status, new.date_applied, new.first_response_date

        if status is Status.SAVED:
            if applied is not None:
                raise ValidationError(
                    "A saved application has no applied date yet. "
                    "Leave the date out, or use the status 'applied'."
                )
        elif applied is None and status is not Status.WITHDRAWN:
            # Withdrawn without a date means it was dropped before applying.
            raise ValidationError(f"The status '{status.value}' needs an applied date.")
        if applied is not None and applied > today:
            raise ValidationError(f"The applied date {applied} is in the future.")

        if first_response is not None:
            if status not in RESPONSE_STATUSES and status is not Status.WITHDRAWN:
                raise ValidationError(
                    f"A first response date does not fit the status '{status.value}'."
                )
            if applied is None:
                raise ValidationError("A first response date needs an applied date.")
            if first_response < applied:
                raise ValidationError(
                    f"The first response date {first_response} is before "
                    f"the applied date {applied}."
                )
            if first_response > today:
                raise ValidationError(
                    f"The first response date {first_response} is in the future."
                )

        return replace(
            new,
            company=company,
            role=role,
            link=clean_optional(new.link),
            location=clean_optional(new.location),
            source=clean_optional(new.source),
            notes=clean_optional(new.notes),
        )

    def add(
        self,
        *,
        company: str,
        role: str,
        today: date,
        link: str | None = None,
        location: str | None = None,
        source: str | None = None,
        notes: str | None = None,
        status: Status = Status.APPLIED,
        date_applied: date | None = None,
    ) -> Application:
        """Add an application as 'applied' (applied date defaults to today) or 'saved'."""
        if status not in (Status.SAVED, Status.APPLIED):
            raise ValidationError(
                f"A new application starts as 'saved' or 'applied', not '{status.value}'. "
                "Add it first, then change its status."
            )
        if status is Status.APPLIED and date_applied is None:
            date_applied = today
        new = self.check_new(
            NewApplication(
                company=company,
                role=role,
                link=link,
                location=location,
                source=source,
                notes=notes,
                status=status,
                date_applied=date_applied,
            ),
            today=today,
        )
        with self.conn:
            app_id = self._insert(new, changed_on=new.date_applied or today, note=None)
        return self.get(app_id)

    def _insert(self, new: NewApplication, *, changed_on: date, note: str | None) -> int:
        """Insert an already checked application with its first history entry."""
        app_id = db.insert_application(self.conn, new, now=_now())
        db.insert_status_change(
            self.conn,
            app_id,
            old_status=None,
            new_status=new.status,
            changed_on=changed_on,
            note=note,
        )
        return app_id

    # --- reading ------------------------------------------------------------

    def get(self, app_id: int) -> Application:
        app = db.get_application(self.conn, app_id)
        if app is None:
            raise NotFoundError(
                f"No application with id {app_id}. Run 'jobtrack list' to see the ids."
            )
        return app

    def history(self, app_id: int) -> list[StatusChange]:
        self.get(app_id)
        return db.get_history(self.conn, app_id)

    def find(
        self,
        *,
        statuses: list[Status] | None = None,
        company: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        sort: str = "id",
        descending: bool = False,
        limit: int | None = None,
    ) -> list[Application]:
        """Applications matching every given filter. Date bounds apply to the applied date."""
        _check_range(date_from, date_to)
        if sort not in db.SORT_KEYS:
            raise ValidationError(
                f"Unknown sort key '{sort}'. Choose one of: {', '.join(db.SORT_KEYS)}."
            )
        if limit is not None and limit < 1:
            raise ValidationError("The limit must be 1 or more.")
        return db.query_applications(
            self.conn,
            statuses=statuses or None,
            company=clean_optional(company),
            date_from=date_from,
            date_to=date_to,
            search=clean_optional(search),
            sort=sort,
            descending=descending,
            limit=limit,
        )

    # --- editing and deleting -----------------------------------------------

    def edit(
        self,
        app_id: int,
        *,
        today: date,
        company: object = UNSET,
        role: object = UNSET,
        link: object = UNSET,
        location: object = UNSET,
        source: object = UNSET,
        notes: object = UNSET,
        date_applied: object = UNSET,
    ) -> Application:
        """Change the given fields. Blank text clears an optional field. Status is not edited here."""
        app = self.get(app_id)
        changes: dict[str, object] = {}
        if company is not UNSET:
            changes["company"] = require_text(company, field="company")
        if role is not UNSET:
            changes["role"] = require_text(role, field="role")
        for column, value in (
            ("link", link),
            ("location", location),
            ("source", source),
            ("notes", notes),
        ):
            if value is not UNSET:
                changes[column] = clean_optional(value)
        if date_applied is not UNSET:
            changes["date_applied"] = self._check_new_applied_date(app, date_applied, today)
        if not changes:
            raise ValidationError("Nothing to change. Give at least one field to edit.")
        with self.conn:
            db.update_application(self.conn, app_id, changes, now=_now())
        return self.get(app_id)

    @staticmethod
    def _check_new_applied_date(app: Application, new_date: date | None, today: date) -> date:
        if app.date_applied is None:
            raise ValidationError(
                f"Application {app.id} is '{app.status.value}' and has no applied date. "
                "Move it to 'applied' first."
            )
        if new_date is None:
            raise ValidationError("The applied date cannot be removed.")
        if new_date > today:
            raise ValidationError(f"The applied date {new_date} is in the future.")
        if app.first_response_date is not None and new_date > app.first_response_date:
            raise ValidationError(
                f"The applied date {new_date} would be after the first response "
                f"on {app.first_response_date}."
            )
        return new_date

    def delete(self, app_id: int) -> Application:
        """Delete an application and its history. Returns what was deleted."""
        app = self.get(app_id)
        with self.conn:
            db.delete_application(self.conn, app_id)
        return app

    # --- status -------------------------------------------------------------

    def change_status(
        self,
        app_id: int,
        new_status: Status,
        *,
        on: date,
        today: date,
        note: str | None = None,
        force: bool = False,
    ) -> Application:
        """Move an application to `new_status`, dated `on`, and record it in the history.

        Besides the status itself this keeps two dates in step:
        - the applied date is set to `on` when the application leaves 'saved'
          for anything but 'withdrawn', and removed on a forced move back to 'saved';
        - the first response date is set the first time a response status is
          reached, and removed on a forced move back to 'saved' or 'applied'.
        """
        app = self.get(app_id)
        old_status = app.status
        status_rules.check_move(old_status, new_status, force=force)
        if on > today:
            raise ValidationError(f"The status change date {on} is in the future.")

        changes: dict[str, object] = {"status": new_status}
        applied = app.date_applied
        if new_status is Status.SAVED:
            applied = None
            changes["date_applied"] = None
        elif applied is None and new_status is not Status.WITHDRAWN:
            applied = on
            changes["date_applied"] = on
        if applied is not None and on < applied:
            raise ValidationError(
                f"The status change date {on} is before the applied date {applied}."
            )

        if new_status in (Status.SAVED, Status.APPLIED):
            if app.first_response_date is not None:
                changes["first_response_date"] = None
        elif new_status in RESPONSE_STATUSES and app.first_response_date is None:
            changes["first_response_date"] = on

        with self.conn:
            db.update_application(self.conn, app_id, changes, now=_now())
            db.insert_status_change(
                self.conn,
                app_id,
                old_status=old_status,
                new_status=new_status,
                changed_on=on,
                note=clean_optional(note),
            )
        return self.get(app_id)

    # --- follow-ups ---------------------------------------------------------

    def set_follow_up(self, app_id: int, when: date | None) -> Application:
        """Set the follow-up date, or remove it with None."""
        self.get(app_id)
        with self.conn:
            db.update_application(self.conn, app_id, {"follow_up_date": when}, now=_now())
        return self.get(app_id)

    def due(self, *, today: date, within: int = 0) -> list[DueItem]:
        """Open applications whose follow-up date is today or earlier, most overdue first.

        `within` also includes follow-ups in the next N days. Rejected and
        withdrawn applications are left out; their date stays stored.
        """
        if within < 0:
            raise ValidationError("The number of days to look ahead cannot be negative.")
        candidates = db.query_applications(
            self.conn, follow_up_until=today + timedelta(days=within), sort="followup"
        )
        return [
            DueItem(application=app, days_overdue=(today - app.follow_up_date).days)
            for app in candidates
            if app.status not in CLOSED_STATUSES
        ]

    # --- summary ------------------------------------------------------------

    def summary(self, *, date_from: date | None = None, date_to: date | None = None) -> Summary:
        """Counts and rates from the stored applications, nothing else.

        - applied: applications that have an applied date ('saved' ones do not).
        - responded: applied applications that have a first response date or
          are currently in a response status (screening, interview, offer, rejected).
        - response rate: responded / applied, None when nothing was applied.
        - average days to first response: mean of first response date minus
          applied date over the responded applications that have both dates.

        With a date range only applications applied inside it are counted.
        """
        _check_range(date_from, date_to)
        apps = db.query_applications(self.conn, date_from=date_from, date_to=date_to)

        by_status = {status: 0 for status in Status}
        for app in apps:
            by_status[app.status] += 1

        applied = [app for app in apps if app.date_applied is not None]
        responded = [
            app
            for app in applied
            if app.first_response_date is not None or app.status in RESPONSE_STATUSES
        ]
        waits = [
            (app.first_response_date - app.date_applied).days
            for app in responded
            if app.first_response_date is not None
        ]
        return Summary(
            total=len(apps),
            by_status=by_status,
            applied=len(applied),
            responded=len(responded),
            response_rate=len(responded) / len(applied) if applied else None,
            avg_days_to_first_response=sum(waits) / len(waits) if waits else None,
            avg_based_on=len(waits),
            date_from=date_from,
            date_to=date_to,
        )


def _check_range(date_from: date | None, date_to: date | None) -> None:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ValidationError(
            f"The date range is backwards: {date_from} is after {date_to}."
        )
