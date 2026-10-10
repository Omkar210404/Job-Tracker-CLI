"""Adding, reading, editing, deleting and filtering through the service layer."""

from datetime import date, timedelta

import pytest

from jobtracker.errors import NotFoundError, ValidationError
from jobtracker.models import NewApplication, Status

from .conftest import TODAY


def add(svc, **overrides):
    values = dict(company="Northwind Example Ltd", role="Junior Python Developer", today=TODAY)
    values.update(overrides)
    return svc.add(**values)


# --- add --------------------------------------------------------------------


def test_add_defaults_to_applied_today(svc):
    app = add(svc)
    assert app.id == 1
    assert app.status is Status.APPLIED
    assert app.date_applied == TODAY
    assert app.follow_up_date is None and app.first_response_date is None


def test_add_keeps_all_fields_and_trims_text(svc):
    app = add(
        svc,
        company="  Northwind Example Ltd ",
        role=" Data Analyst ",
        link=" https://example.com/jobs/7 ",
        location=" Mumbai ",
        source=" referral ",
        notes="  ",
        date_applied=date(2026, 3, 2),
    )
    assert app.company == "Northwind Example Ltd"
    assert app.role == "Data Analyst"
    assert app.link == "https://example.com/jobs/7"
    assert app.location == "Mumbai"
    assert app.source == "referral"
    assert app.notes is None
    assert app.date_applied == date(2026, 3, 2)


def test_add_saved_has_no_applied_date(svc):
    app = add(svc, status=Status.SAVED)
    assert app.status is Status.SAVED
    assert app.date_applied is None


def test_add_saved_with_applied_date_is_refused(svc):
    with pytest.raises(ValidationError, match="saved application has no applied date"):
        add(svc, status=Status.SAVED, date_applied=TODAY)


@pytest.mark.parametrize("field", ["company", "role"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_add_refuses_blank_required_fields(svc, field, blank):
    with pytest.raises(ValidationError, match=f"{field} cannot be empty"):
        add(svc, **{field: blank})
    assert svc.find() == []


def test_add_refuses_future_applied_date(svc):
    with pytest.raises(ValidationError, match="in the future"):
        add(svc, date_applied=TODAY + timedelta(days=1))


@pytest.mark.parametrize(
    "status",
    [Status.SCREENING, Status.INTERVIEW, Status.OFFER, Status.REJECTED, Status.WITHDRAWN],
)
def test_add_only_starts_as_saved_or_applied(svc, status):
    with pytest.raises(ValidationError, match="starts as 'saved' or 'applied'"):
        add(svc, status=status)


def test_add_records_first_history_entry(svc):
    applied = add(svc, date_applied=date(2026, 3, 2))
    saved = add(svc, status=Status.SAVED)

    (entry,) = svc.history(applied.id)
    assert entry.old_status is None and entry.new_status is Status.APPLIED
    assert entry.changed_on == date(2026, 3, 2)

    (entry,) = svc.history(saved.id)
    assert entry.new_status is Status.SAVED
    assert entry.changed_on == TODAY


# --- check_new (also used by the CSV import) ---------------------------------


def new(**overrides):
    values = dict(
        company="Northwind Example Ltd",
        role="Data Analyst",
        status=Status.INTERVIEW,
        date_applied=date(2026, 3, 2),
    )
    values.update(overrides)
    return NewApplication(**values)


def test_check_new_accepts_a_consistent_row(svc):
    checked = svc.check_new(new(first_response_date=date(2026, 3, 9)), today=TODAY)
    assert checked.first_response_date == date(2026, 3, 9)


@pytest.mark.parametrize(
    "status", [Status.APPLIED, Status.SCREENING, Status.INTERVIEW, Status.OFFER, Status.REJECTED]
)
def test_check_new_needs_applied_date_once_applied(svc, status):
    with pytest.raises(ValidationError, match="needs an applied date"):
        svc.check_new(new(status=status, date_applied=None), today=TODAY)


def test_check_new_allows_withdrawn_before_applying(svc):
    svc.check_new(new(status=Status.WITHDRAWN, date_applied=None), today=TODAY)


@pytest.mark.parametrize("status", [Status.SAVED, Status.APPLIED])
def test_check_new_refuses_first_response_on_unanswered_status(svc, status):
    applied = None if status is Status.SAVED else date(2026, 3, 2)
    with pytest.raises(ValidationError, match="first response date does not fit"):
        svc.check_new(
            new(status=status, date_applied=applied, first_response_date=date(2026, 3, 9)),
            today=TODAY,
        )


def test_check_new_refuses_first_response_before_applied(svc):
    with pytest.raises(ValidationError, match="before the applied date"):
        svc.check_new(new(first_response_date=date(2026, 3, 1)), today=TODAY)


def test_check_new_refuses_first_response_in_future(svc):
    with pytest.raises(ValidationError, match="in the future"):
        svc.check_new(new(first_response_date=TODAY + timedelta(days=3)), today=TODAY)


def test_check_new_refuses_first_response_without_applied_date(svc):
    with pytest.raises(ValidationError, match="needs an applied date"):
        svc.check_new(
            new(status=Status.WITHDRAWN, date_applied=None, first_response_date=date(2026, 3, 9)),
            today=TODAY,
        )


# --- get / history ----------------------------------------------------------


def test_get_unknown_id(svc):
    with pytest.raises(NotFoundError, match="No application with id 7"):
        svc.get(7)


def test_history_unknown_id(svc):
    with pytest.raises(NotFoundError):
        svc.history(7)


# --- edit -------------------------------------------------------------------


def test_edit_changes_only_given_fields(svc):
    app = add(svc, location="Pune", notes="first note", date_applied=date(2026, 3, 2))
    edited = svc.edit(app.id, today=TODAY, role="Python Developer", notes="second note")
    assert edited.role == "Python Developer"
    assert edited.notes == "second note"
    assert edited.company == app.company
    assert edited.location == "Pune"
    assert edited.status is Status.APPLIED
    assert edited.date_applied == date(2026, 3, 2)


def test_edit_blank_text_clears_optional_field(svc):
    app = add(svc, location="Pune", link="https://example.com/x")
    edited = svc.edit(app.id, today=TODAY, location="", link=None)
    assert edited.location is None and edited.link is None


@pytest.mark.parametrize("field", ["company", "role"])
def test_edit_refuses_blank_required_fields(svc, field):
    app = add(svc)
    with pytest.raises(ValidationError, match=f"{field} cannot be empty"):
        svc.edit(app.id, today=TODAY, **{field: " "})
    assert svc.get(app.id) == app


def test_edit_with_nothing_to_change(svc):
    app = add(svc)
    with pytest.raises(ValidationError, match="Nothing to change"):
        svc.edit(app.id, today=TODAY)


def test_edit_unknown_id(svc):
    with pytest.raises(NotFoundError):
        svc.edit(3, today=TODAY, notes="x")


def test_edit_applied_date(svc):
    app = add(svc, date_applied=date(2026, 3, 2))
    assert svc.edit(app.id, today=TODAY, date_applied=date(2026, 3, 1)).date_applied == date(
        2026, 3, 1
    )


def test_edit_applied_date_refusals(svc):
    app = add(svc, date_applied=date(2026, 3, 2))
    saved = add(svc, status=Status.SAVED)
    with pytest.raises(ValidationError, match="in the future"):
        svc.edit(app.id, today=TODAY, date_applied=TODAY + timedelta(days=1))
    with pytest.raises(ValidationError, match="cannot be removed"):
        svc.edit(app.id, today=TODAY, date_applied=None)
    with pytest.raises(ValidationError, match="has no applied date"):
        svc.edit(saved.id, today=TODAY, date_applied=date(2026, 3, 1))


# --- delete -----------------------------------------------------------------


def test_delete_returns_and_removes_the_application(svc):
    keep = add(svc, company="Keep Example Co")
    gone = add(svc, company="Gone Example Co")
    deleted = svc.delete(gone.id)
    assert deleted == gone
    assert [app.id for app in svc.find()] == [keep.id]
    with pytest.raises(NotFoundError):
        svc.history(gone.id)


def test_delete_unknown_id(svc):
    with pytest.raises(NotFoundError):
        svc.delete(1)


# --- find -------------------------------------------------------------------


@pytest.fixture
def several(svc):
    add(svc, company="Alpha Example", role="Data Analyst", date_applied=date(2026, 2, 10),
        location="Mumbai")
    add(svc, company="Beta Example", role="ML Intern", date_applied=date(2026, 3, 1),
        location="Remote")
    add(svc, company="Gamma Example", role="Backend Developer", date_applied=date(2026, 3, 12),
        location="Pune")
    add(svc, company="Delta Example", role="Python Developer", status=Status.SAVED)
    return svc


def ids(apps):
    return [app.id for app in apps]


def test_find_all(several):
    assert ids(several.find()) == [1, 2, 3, 4]


def test_find_by_each_filter(several):
    assert ids(several.find(statuses=[Status.SAVED])) == [4]
    assert ids(several.find(company="beta")) == [2]
    assert ids(several.find(date_from=date(2026, 3, 1))) == [2, 3]
    assert ids(several.find(date_to=date(2026, 3, 1))) == [1, 2]
    assert ids(several.find(search="developer")) == [3, 4]


def test_find_combined_filters(several):
    found = several.find(statuses=[Status.APPLIED], search="developer", date_from=date(2026, 3, 1))
    assert ids(found) == [3]


def test_find_blank_text_filters_are_ignored(several):
    assert ids(several.find(company="  ", search="")) == [1, 2, 3, 4]


def test_find_sort_and_limit(several):
    assert ids(several.find(sort="company", limit=2)) == [1, 2]
    assert ids(several.find(sort="applied", descending=True)) == [3, 2, 1, 4]


def test_find_refuses_backwards_range(several):
    with pytest.raises(ValidationError, match="backwards"):
        several.find(date_from=date(2026, 3, 10), date_to=date(2026, 3, 1))


def test_find_refuses_unknown_sort_and_bad_limit(several):
    with pytest.raises(ValidationError, match="Unknown sort key"):
        several.find(sort="salary")
    with pytest.raises(ValidationError, match="limit"):
        several.find(limit=0)
