from datetime import date

import pytest

from jobtracker.errors import ValidationError
from jobtracker.models import Status
from jobtracker.validation import (
    clean_optional,
    parse_date,
    parse_optional_date,
    parse_status,
    require_text,
)

from .conftest import TODAY


def test_parse_date_accepts_iso():
    assert parse_date("2026-03-10", field="applied date", today=TODAY) == date(2026, 3, 10)


def test_parse_date_trims_spaces():
    assert parse_date("  2026-03-10 ", field="applied date", today=TODAY) == date(2026, 3, 10)


@pytest.mark.parametrize("text", ["today", "Today", "TODAY"])
def test_parse_date_accepts_today(text):
    assert parse_date(text, field="applied date", today=TODAY) == TODAY


@pytest.mark.parametrize(
    "text",
    ["10/03/2026", "10-03-2026", "2026/03/10", "20260310", "2026-3-10", "March 10", "tomorrow"],
)
def test_parse_date_rejects_other_formats(text):
    with pytest.raises(ValidationError) as excinfo:
        parse_date(text, field="applied date", today=TODAY)
    message = str(excinfo.value)
    assert "applied date" in message
    assert text in message
    assert "YYYY-MM-DD" in message


@pytest.mark.parametrize("text", ["2026-02-30", "2026-13-01", "2025-02-29", "2026-00-10"])
def test_parse_date_rejects_days_that_do_not_exist(text):
    with pytest.raises(ValidationError, match="does not exist"):
        parse_date(text, field="follow-up date", today=TODAY)


def test_parse_date_accepts_leap_day():
    assert parse_date("2024-02-29", field="applied date", today=TODAY) == date(2024, 2, 29)


@pytest.mark.parametrize("text", ["", "   ", None])
def test_parse_date_rejects_empty(text):
    with pytest.raises(ValidationError, match="empty"):
        parse_date(text, field="applied date", today=TODAY)


def test_relative_days_only_when_allowed():
    assert parse_date("+7d", field="follow-up date", today=TODAY, allow_relative=True) == date(
        2026, 3, 27
    )
    assert parse_date("+0d", field="follow-up date", today=TODAY, allow_relative=True) == TODAY
    with pytest.raises(ValidationError):
        parse_date("+7d", field="applied date", today=TODAY)


@pytest.mark.parametrize("text", ["+7", "7d", "-7d", "+d", "+1.5d"])
def test_malformed_relative_days_rejected(text):
    with pytest.raises(ValidationError, match=r"\+7d"):
        parse_date(text, field="follow-up date", today=TODAY, allow_relative=True)


def test_relative_days_cross_month_end():
    assert parse_date("+14d", field="follow-up date", today=TODAY, allow_relative=True) == date(
        2026, 4, 3
    )


@pytest.mark.parametrize("text", [None, "", "  "])
def test_parse_optional_date_blank_is_none(text):
    assert parse_optional_date(text, field="follow-up date", today=TODAY) is None


def test_parse_optional_date_still_validates():
    assert parse_optional_date("2026-03-01", field="x", today=TODAY) == date(2026, 3, 1)
    with pytest.raises(ValidationError):
        parse_optional_date("soon", field="x", today=TODAY)


@pytest.mark.parametrize("status", list(Status))
def test_parse_status_accepts_every_status(status):
    assert parse_status(status.value) is status


def test_parse_status_ignores_case_and_spaces():
    assert parse_status("  Interview ") is Status.INTERVIEW


def test_parse_status_unknown_lists_valid_ones():
    with pytest.raises(ValidationError) as excinfo:
        parse_status("hired")
    message = str(excinfo.value)
    assert "'hired'" in message
    for status in Status:
        assert status.value in message


def test_parse_status_suggests_close_match():
    with pytest.raises(ValidationError, match="Did you mean 'interview'"):
        parse_status("intervew")


def test_require_text():
    assert require_text("  Acme  ", field="company") == "Acme"
    for blank in (None, "", "   "):
        with pytest.raises(ValidationError, match="company cannot be empty"):
            require_text(blank, field="company")


def test_clean_optional():
    assert clean_optional(None) is None
    assert clean_optional("   ") is None
    assert clean_optional(" remote ") == "remote"
