"""Follow-up dates and the due / overdue list, with a fixed "today"."""

from datetime import date, timedelta

import pytest

from jobtracker.errors import NotFoundError, ValidationError
from jobtracker.models import Status

from .conftest import TODAY

APPLIED_ON = date(2026, 3, 2)


def add(svc, company, follow_up=None, **kwargs):
    app = svc.add(company=company, role="Developer", today=TODAY, date_applied=APPLIED_ON, **kwargs)
    if follow_up is not None:
        app = svc.set_follow_up(app.id, follow_up)
    return app


def days(delta):
    return TODAY + timedelta(days=delta)


def due_view(items):
    return [(item.application.company, item.days_overdue) for item in items]


# --- setting ----------------------------------------------------------------


def test_set_change_and_clear(svc):
    app = add(svc, "Northwind Example Ltd")
    assert app.follow_up_date is None

    assert svc.set_follow_up(app.id, days(7)).follow_up_date == days(7)
    assert svc.set_follow_up(app.id, days(3)).follow_up_date == days(3)
    assert svc.set_follow_up(app.id, None).follow_up_date is None
    # Clearing twice is harmless.
    assert svc.set_follow_up(app.id, None).follow_up_date is None


def test_set_does_not_touch_other_fields(svc):
    app = add(svc, "Northwind Example Ltd", notes="keep me")
    updated = svc.set_follow_up(app.id, days(2))
    assert (updated.status, updated.notes, updated.date_applied) == (
        Status.APPLIED, "keep me", APPLIED_ON,
    )
    assert len(svc.history(app.id)) == 1


def test_set_on_unknown_id(svc):
    with pytest.raises(NotFoundError):
        svc.set_follow_up(4, TODAY)


def test_a_past_follow_up_date_is_accepted_and_shows_as_overdue(svc):
    add(svc, "Northwind Example Ltd", follow_up=days(-2))
    assert due_view(svc.due(today=TODAY)) == [("Northwind Example Ltd", 2)]


# --- due list ---------------------------------------------------------------


def test_nothing_due_on_empty_database(svc):
    assert svc.due(today=TODAY) == []


def test_applications_without_follow_up_are_never_due(svc):
    add(svc, "Northwind Example Ltd")
    assert svc.due(today=TODAY, within=365) == []


def test_due_boundaries(svc):
    add(svc, "Yesterday Example", follow_up=days(-1))
    add(svc, "Today Example", follow_up=days(0))
    add(svc, "Tomorrow Example", follow_up=days(1))
    assert due_view(svc.due(today=TODAY)) == [("Yesterday Example", 1), ("Today Example", 0)]


def test_overdue_day_count(svc):
    add(svc, "Long Ago Example", follow_up=date(2026, 2, 18))
    (item,) = svc.due(today=TODAY)
    assert item.days_overdue == 30


def test_most_overdue_first(svc):
    add(svc, "Second Example", follow_up=days(-2))
    add(svc, "Third Example", follow_up=days(0))
    add(svc, "First Example", follow_up=days(-9))
    assert due_view(svc.due(today=TODAY)) == [
        ("First Example", 9), ("Second Example", 2), ("Third Example", 0),
    ]


def test_within_includes_upcoming_days(svc):
    add(svc, "Overdue Example", follow_up=days(-1))
    add(svc, "In Three Example", follow_up=days(3))
    add(svc, "In Four Example", follow_up=days(4))
    assert due_view(svc.due(today=TODAY, within=3)) == [
        ("Overdue Example", 1), ("In Three Example", -3),
    ]


def test_negative_within_is_refused(svc):
    with pytest.raises(ValidationError, match="cannot be negative"):
        svc.due(today=TODAY, within=-1)


@pytest.mark.parametrize("closed", [Status.REJECTED, Status.WITHDRAWN])
def test_closed_applications_are_left_out_but_keep_their_date(svc, closed):
    app = add(svc, "Closed Example", follow_up=days(-3))
    add(svc, "Open Example", follow_up=days(-1))
    svc.change_status(app.id, closed, on=TODAY, today=TODAY)

    assert due_view(svc.due(today=TODAY)) == [("Open Example", 1)]
    assert svc.get(app.id).follow_up_date == days(-3)


@pytest.mark.parametrize(
    "status", [Status.SCREENING, Status.INTERVIEW, Status.OFFER]
)
def test_open_applications_in_any_stage_are_listed(svc, status):
    app = add(svc, "Open Example", follow_up=days(-1))
    svc.change_status(app.id, status, on=TODAY, today=TODAY)
    assert due_view(svc.due(today=TODAY)) == [("Open Example", 1)]


def test_saved_application_can_have_a_follow_up(svc):
    app = svc.add(company="Saved Example", role="Developer", today=TODAY, status=Status.SAVED)
    svc.set_follow_up(app.id, TODAY)
    assert due_view(svc.due(today=TODAY)) == [("Saved Example", 0)]


def test_reopened_application_is_due_again(svc):
    app = add(svc, "Reopened Example", follow_up=days(-3))
    svc.change_status(app.id, Status.REJECTED, on=TODAY, today=TODAY)
    assert svc.due(today=TODAY) == []
    svc.change_status(app.id, Status.INTERVIEW, on=TODAY, today=TODAY, force=True)
    assert due_view(svc.due(today=TODAY)) == [("Reopened Example", 3)]


def test_the_same_data_gives_different_answers_on_different_days(svc):
    add(svc, "Northwind Example Ltd", follow_up=date(2026, 3, 25))
    assert svc.due(today=date(2026, 3, 24)) == []
    assert due_view(svc.due(today=date(2026, 3, 25))) == [("Northwind Example Ltd", 0)]
    assert due_view(svc.due(today=date(2026, 4, 1))) == [("Northwind Example Ltd", 7)]
