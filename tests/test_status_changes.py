"""Status changes through the service: the rules applied to stored applications,
the dates that move with them, and the history that is written."""

from datetime import date, timedelta

import pytest

from jobtracker.errors import NotFoundError, ValidationError
from jobtracker.models import Status

from .conftest import TODAY

S = Status
APPLIED_ON = date(2026, 3, 2)


@pytest.fixture
def applied(svc):
    return svc.add(
        company="Northwind Example Ltd", role="Data Analyst", today=TODAY, date_applied=APPLIED_ON
    )


@pytest.fixture
def saved(svc):
    return svc.add(
        company="Bluefield Example Co", role="ML Intern", today=TODAY, status=S.SAVED
    )


def move(svc, app, status, on=TODAY, **kwargs):
    return svc.change_status(app.id, status, on=on, today=TODAY, **kwargs)


def test_allowed_move_updates_status(svc, applied):
    moved = move(svc, applied, S.SCREENING, on=date(2026, 3, 9))
    assert moved.status is S.SCREENING
    assert moved.date_applied == APPLIED_ON


def test_full_pipeline_walk(svc, saved):
    days = iter(date(2026, 3, d) for d in (3, 6, 10, 15, 19))
    for status in (S.APPLIED, S.SCREENING, S.INTERVIEW, S.OFFER):
        saved = move(svc, saved, status, on=next(days))
    assert saved.status is S.OFFER
    assert saved.date_applied == date(2026, 3, 3)
    assert saved.first_response_date == date(2026, 3, 6)
    assert [entry.new_status for entry in svc.history(saved.id)] == [
        S.SAVED, S.APPLIED, S.SCREENING, S.INTERVIEW, S.OFFER,
    ]


def test_refused_move_changes_nothing(svc, applied):
    interview = move(svc, applied, S.INTERVIEW, on=date(2026, 3, 9))
    with pytest.raises(ValidationError, match="from 'interview' to 'applied'"):
        move(svc, interview, S.APPLIED)
    assert svc.get(applied.id) == interview
    assert len(svc.history(applied.id)) == 2


def test_closed_application_does_not_move_without_force(svc, applied):
    rejected = move(svc, applied, S.REJECTED, on=date(2026, 3, 9))
    with pytest.raises(ValidationError, match="closed status"):
        move(svc, rejected, S.INTERVIEW)
    assert move(svc, rejected, S.INTERVIEW, force=True).status is S.INTERVIEW


def test_same_status_is_refused(svc, applied):
    with pytest.raises(ValidationError, match="already 'applied'"):
        move(svc, applied, S.APPLIED)


def test_unknown_id(svc):
    with pytest.raises(NotFoundError):
        svc.change_status(9, S.APPLIED, on=TODAY, today=TODAY)


# --- dates ------------------------------------------------------------------


def test_saved_to_applied_sets_applied_date(svc, saved):
    moved = move(svc, saved, S.APPLIED, on=date(2026, 3, 18))
    assert moved.date_applied == date(2026, 3, 18)


def test_saved_to_withdrawn_leaves_applied_date_empty(svc, saved):
    moved = move(svc, saved, S.WITHDRAWN)
    assert moved.status is S.WITHDRAWN
    assert moved.date_applied is None
    assert moved.first_response_date is None


def test_change_dated_before_applied_date_is_refused(svc, applied):
    with pytest.raises(ValidationError, match="before the applied date"):
        move(svc, applied, S.SCREENING, on=APPLIED_ON - timedelta(days=1))
    assert svc.get(applied.id).status is S.APPLIED


def test_change_on_the_applied_date_itself_is_fine(svc, applied):
    assert move(svc, applied, S.REJECTED, on=APPLIED_ON).first_response_date == APPLIED_ON


def test_change_dated_in_the_future_is_refused(svc, applied):
    with pytest.raises(ValidationError, match="in the future"):
        move(svc, applied, S.SCREENING, on=TODAY + timedelta(days=1))


# --- first response date ----------------------------------------------------


@pytest.mark.parametrize("status", [S.SCREENING, S.INTERVIEW, S.OFFER, S.REJECTED])
def test_first_response_date_set_on_first_response_status(svc, applied, status):
    moved = move(svc, applied, status, on=date(2026, 3, 11))
    assert moved.first_response_date == date(2026, 3, 11)


def test_withdrawing_is_not_a_response(svc, applied):
    assert move(svc, applied, S.WITHDRAWN, on=date(2026, 3, 11)).first_response_date is None


def test_first_response_date_is_not_overwritten_by_later_moves(svc, applied):
    move(svc, applied, S.SCREENING, on=date(2026, 3, 9))
    move(svc, applied, S.INTERVIEW, on=date(2026, 3, 14))
    final = move(svc, applied, S.REJECTED, on=date(2026, 3, 19))
    assert final.first_response_date == date(2026, 3, 9)


def test_withdrawing_after_a_response_keeps_the_date(svc, applied):
    move(svc, applied, S.INTERVIEW, on=date(2026, 3, 9))
    assert move(svc, applied, S.WITHDRAWN, on=date(2026, 3, 12)).first_response_date == date(
        2026, 3, 9
    )


# --- force ------------------------------------------------------------------


def test_forced_move_back_to_applied_clears_first_response(svc, applied):
    move(svc, applied, S.SCREENING, on=date(2026, 3, 9))
    back = move(svc, applied, S.APPLIED, on=date(2026, 3, 10), force=True)
    assert back.status is S.APPLIED
    assert back.first_response_date is None
    assert back.date_applied == APPLIED_ON
    # The next real response counts as the first one again.
    assert move(svc, applied, S.INTERVIEW, on=date(2026, 3, 15)).first_response_date == date(
        2026, 3, 15
    )


def test_forced_move_back_to_saved_clears_both_dates(svc, applied):
    move(svc, applied, S.SCREENING, on=date(2026, 3, 9))
    back = move(svc, applied, S.SAVED, on=date(2026, 3, 10), force=True)
    assert back.status is S.SAVED
    assert back.date_applied is None
    assert back.first_response_date is None


def test_forced_backward_move_within_responses_keeps_first_response(svc, applied):
    move(svc, applied, S.INTERVIEW, on=date(2026, 3, 9))
    back = move(svc, applied, S.SCREENING, on=date(2026, 3, 10), force=True)
    assert back.first_response_date == date(2026, 3, 9)


def test_forced_skip_from_saved_sets_applied_date(svc, saved):
    moved = move(svc, saved, S.INTERVIEW, on=date(2026, 3, 12), force=True)
    assert moved.date_applied == date(2026, 3, 12)
    assert moved.first_response_date == date(2026, 3, 12)


def test_force_does_not_bypass_date_checks(svc, applied):
    with pytest.raises(ValidationError, match="before the applied date"):
        move(svc, applied, S.OFFER, on=date(2026, 3, 1), force=True)
    with pytest.raises(ValidationError, match="in the future"):
        move(svc, applied, S.OFFER, on=TODAY + timedelta(days=2), force=True)


# --- history ----------------------------------------------------------------


def test_each_change_is_recorded_with_date_and_note(svc, applied):
    move(svc, applied, S.SCREENING, on=date(2026, 3, 9), note="  recruiter call  ")
    move(svc, applied, S.APPLIED, on=date(2026, 3, 10), force=True)
    first, second, third = svc.history(applied.id)
    assert (first.old_status, first.new_status, first.changed_on) == (None, S.APPLIED, APPLIED_ON)
    assert (second.old_status, second.new_status) == (S.APPLIED, S.SCREENING)
    assert second.changed_on == date(2026, 3, 9)
    assert second.note == "recruiter call"
    assert (third.old_status, third.new_status) == (S.SCREENING, S.APPLIED)
    assert third.note is None
