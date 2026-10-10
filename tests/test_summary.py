"""Summary numbers, each checked against a value worked out by hand in the test."""

from datetime import date

import pytest

from jobtracker import db
from jobtracker.errors import ValidationError
from jobtracker.models import NewApplication, Status

from .conftest import TODAY

S = Status


def add(svc, applied_on, company="Northwind Example Ltd", **kwargs):
    return svc.add(company=company, role="Developer", today=TODAY, date_applied=applied_on, **kwargs)


def move(svc, app, status, on, **kwargs):
    return svc.change_status(app.id, status, on=on, today=TODAY, **kwargs)


def d(day, month=3):
    return date(2026, month, day)


# --- empty and nearly empty -------------------------------------------------


def test_empty_database_gives_zeros_and_no_rates(svc):
    summary = svc.summary()
    assert summary.total == 0
    assert summary.by_status == {status: 0 for status in Status}
    assert summary.applied == 0 and summary.responded == 0
    assert summary.response_rate is None
    assert summary.avg_days_to_first_response is None
    assert summary.avg_based_on == 0


def test_only_saved_applications_means_nothing_to_divide_by(svc):
    svc.add(company="Saved Example", role="Developer", today=TODAY, status=S.SAVED)
    summary = svc.summary()
    assert summary.total == 1
    assert summary.by_status[S.SAVED] == 1
    assert summary.applied == 0
    assert summary.response_rate is None


def test_applied_but_no_response_yet(svc):
    add(svc, d(2))
    add(svc, d(5))
    summary = svc.summary()
    assert summary.applied == 2 and summary.responded == 0
    assert summary.response_rate == 0.0
    assert summary.avg_days_to_first_response is None
    assert summary.avg_based_on == 0


# --- a worked example -------------------------------------------------------


@pytest.fixture
def worked(svc):
    """Eight applications:

    #  status      applied   first response   wait (days)
    1  saved       -         -                -
    2  applied     Mar 02    -                -
    3  applied     Mar 04    -                -
    4  screening   Mar 01    Mar 06           5
    5  interview   Mar 02    Mar 05 (screening), then interview Mar 12   3
    6  offer       Feb 20    Feb 27 (interview), then offer Mar 10       7
    7  rejected    Mar 03    Mar 18           15
    8  withdrawn   Mar 05    -                - (withdrawn before any answer)

    applied   = 7 (all but #1)
    responded = 4 (#4 #5 #6 #7)
    rate      = 4 / 7
    average   = (5 + 3 + 7 + 15) / 4 = 7.5
    """
    svc.add(company="One Example", role="Developer", today=TODAY, status=S.SAVED)
    add(svc, d(2), "Two Example")
    add(svc, d(4), "Three Example")
    move(svc, add(svc, d(1), "Four Example"), S.SCREENING, d(6))
    five = add(svc, d(2), "Five Example")
    move(svc, five, S.SCREENING, d(5))
    move(svc, five, S.INTERVIEW, d(12))
    six = add(svc, d(20, 2), "Six Example")
    move(svc, six, S.INTERVIEW, d(27, 2))
    move(svc, six, S.OFFER, d(10))
    move(svc, add(svc, d(3), "Seven Example"), S.REJECTED, d(18))
    move(svc, add(svc, d(5), "Eight Example"), S.WITHDRAWN, d(9))
    return svc


def test_counts_per_status_include_zeros(worked):
    summary = worked.summary()
    assert summary.total == 8
    assert summary.by_status == {
        S.SAVED: 1,
        S.APPLIED: 2,
        S.SCREENING: 1,
        S.INTERVIEW: 1,
        S.OFFER: 1,
        S.REJECTED: 1,
        S.WITHDRAWN: 1,
    }
    assert sum(summary.by_status.values()) == summary.total


def test_response_rate(worked):
    summary = worked.summary()
    assert summary.applied == 7
    assert summary.responded == 4
    assert summary.response_rate == pytest.approx(4 / 7)


def test_average_days_to_first_response(worked):
    summary = worked.summary()
    assert summary.avg_based_on == 4
    assert summary.avg_days_to_first_response == pytest.approx(7.5)


# --- single rules -----------------------------------------------------------


def test_saved_is_not_counted_as_applied(svc):
    svc.add(company="Saved Example", role="Developer", today=TODAY, status=S.SAVED)
    move(svc, add(svc, d(2)), S.REJECTED, d(4))
    summary = svc.summary()
    assert summary.total == 2 and summary.applied == 1
    assert summary.response_rate == 1.0


def test_withdrawn_alone_is_not_a_response(svc):
    move(svc, add(svc, d(2)), S.WITHDRAWN, d(4))
    summary = svc.summary()
    assert (summary.applied, summary.responded) == (1, 0)
    assert summary.response_rate == 0.0


def test_withdrawn_after_a_response_still_counts_as_responded(svc):
    app = add(svc, d(2))
    move(svc, app, S.INTERVIEW, d(8))
    move(svc, app, S.WITHDRAWN, d(10))
    summary = svc.summary()
    assert (summary.applied, summary.responded) == (1, 1)
    assert summary.avg_days_to_first_response == 6.0


def test_dropped_before_applying_is_not_counted_as_applied(svc):
    saved = svc.add(company="Dropped Example", role="Developer", today=TODAY, status=S.SAVED)
    move(svc, saved, S.WITHDRAWN, TODAY)
    summary = svc.summary()
    assert summary.total == 1 and summary.applied == 0
    assert summary.response_rate is None


def test_wait_uses_the_first_response_not_the_latest(svc):
    app = add(svc, d(1))
    move(svc, app, S.SCREENING, d(3))
    move(svc, app, S.REJECTED, d(19))
    assert svc.summary().avg_days_to_first_response == 2.0


def test_same_day_response_counts_as_zero_days(svc):
    move(svc, add(svc, d(2)), S.REJECTED, d(2))
    summary = svc.summary()
    assert summary.avg_days_to_first_response == 0.0
    assert summary.avg_based_on == 1


def test_forced_correction_back_to_applied_removes_the_response(svc):
    app = add(svc, d(2))
    move(svc, app, S.SCREENING, d(5))
    assert svc.summary().responded == 1
    move(svc, app, S.APPLIED, d(6), force=True)
    summary = svc.summary()
    assert summary.responded == 0
    assert summary.avg_days_to_first_response is None


def test_responded_row_without_a_response_date_counts_for_the_rate_only(svc, conn):
    """An imported application can be 'interview' with no known first response date."""
    move(svc, add(svc, d(2)), S.SCREENING, d(6))
    with conn:
        db.insert_application(
            conn,
            NewApplication(
                company="Imported Example", role="Developer", status=S.INTERVIEW, date_applied=d(1)
            ),
            now="2026-03-20T09:00:00",
        )
    summary = svc.summary()
    assert (summary.applied, summary.responded) == (2, 2)
    assert summary.response_rate == 1.0
    assert summary.avg_based_on == 1
    assert summary.avg_days_to_first_response == 4.0


# --- date range -------------------------------------------------------------


def test_date_range_counts_only_applications_applied_inside_it(worked):
    # Applied Mar 02 .. Mar 04: #2, #3, #5, #7. Responded: #5 (3 days), #7 (15 days).
    summary = worked.summary(date_from=d(2), date_to=d(4))
    assert summary.total == 4
    assert summary.by_status[S.SAVED] == 0
    assert summary.by_status[S.APPLIED] == 2
    assert summary.applied == 4 and summary.responded == 2
    assert summary.response_rate == 0.5
    assert summary.avg_days_to_first_response == 9.0
    assert (summary.date_from, summary.date_to) == (d(2), d(4))


def test_open_ended_ranges(worked):
    assert worked.summary(date_to=d(28, 2)).total == 1
    assert worked.summary(date_from=d(5)).total == 1


def test_range_with_no_applications(worked):
    summary = worked.summary(date_from=date(2025, 1, 1), date_to=date(2025, 12, 31))
    assert summary.total == 0 and summary.response_rate is None


def test_backwards_range_is_refused(svc):
    with pytest.raises(ValidationError, match="backwards"):
        svc.summary(date_from=d(10), date_to=d(1))
