"""Database layer on its own: rows in, rows out, no business rules."""

import sqlite3
from datetime import date

import pytest

from jobtracker import db
from jobtracker.models import NewApplication, Status

NOW = "2026-03-20T09:00:00"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    connection = db.connect(db_path)
    yield connection
    connection.close()


def make(**overrides) -> NewApplication:
    values = dict(
        company="Northwind Example Ltd",
        role="Junior Python Developer",
        status=Status.APPLIED,
        date_applied=date(2026, 3, 2),
    )
    values.update(overrides)
    return NewApplication(**values)


def add(conn, **overrides) -> int:
    with conn:
        return db.insert_application(conn, make(**overrides), now=NOW)


# --- schema -----------------------------------------------------------------


def test_connect_creates_file_and_tables(db_path):
    assert not db_path.exists()
    conn = db.connect(db_path)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"applications", "status_history"} <= tables
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    finally:
        conn.close()
    assert db_path.exists()


def test_connect_creates_missing_parent_folder(tmp_path):
    path = tmp_path / "nested" / "deeper" / "jobs.db"
    conn = db.connect(path)
    conn.close()
    assert path.exists()


def test_reopening_keeps_existing_rows(db_path):
    conn = db.connect(db_path)
    app_id = add(conn)
    conn.close()

    conn = db.connect(db_path)
    try:
        assert db.get_application(conn, app_id).company == "Northwind Example Ltd"
    finally:
        conn.close()


def test_newer_schema_version_is_refused(db_path):
    conn = db.connect(db_path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(sqlite3.DatabaseError, match="schema version 99"):
        db.connect(db_path)


def test_foreign_keys_are_enabled(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# --- insert / get -----------------------------------------------------------


def test_insert_and_get_round_trip(conn):
    app_id = add(
        conn,
        link="https://example.com/jobs/1",
        location="Mumbai",
        source="referral",
        notes="synthetic",
        follow_up_date=date(2026, 3, 16),
        first_response_date=None,
    )
    app = db.get_application(conn, app_id)
    assert app.id == app_id
    assert app.company == "Northwind Example Ltd"
    assert app.role == "Junior Python Developer"
    assert app.link == "https://example.com/jobs/1"
    assert app.location == "Mumbai"
    assert app.source == "referral"
    assert app.notes == "synthetic"
    assert app.status is Status.APPLIED
    assert app.date_applied == date(2026, 3, 2)
    assert app.follow_up_date == date(2026, 3, 16)
    assert app.first_response_date is None
    assert app.created_at == NOW and app.updated_at == NOW


def test_optional_fields_default_to_none(conn):
    app = db.get_application(conn, add(conn, status=Status.SAVED, date_applied=None))
    assert app.link is None and app.location is None and app.source is None
    assert app.notes is None and app.date_applied is None and app.follow_up_date is None


def test_get_unknown_id_returns_none(conn):
    assert db.get_application(conn, 999) is None


def test_check_constraint_refuses_unknown_status(conn):
    add(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE applications SET status = 'hired' WHERE id = 1")


@pytest.mark.parametrize("field", ["company", "role"])
def test_check_constraint_refuses_blank_required_text(conn, field):
    with pytest.raises(sqlite3.IntegrityError):
        add(conn, **{field: "   "})


def test_failed_transaction_leaves_nothing_behind(conn):
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            db.insert_application(conn, make(), now=NOW)
            db.insert_application(conn, make(company=" "), now=NOW)
    assert db.query_applications(conn) == []


# --- update -----------------------------------------------------------------


def test_update_changes_only_given_columns(conn):
    app_id = add(conn, location="Pune")
    later = "2026-03-21T10:00:00"
    with conn:
        assert db.update_application(
            conn,
            app_id,
            {"status": Status.INTERVIEW, "follow_up_date": date(2026, 3, 25)},
            now=later,
        )
    app = db.get_application(conn, app_id)
    assert app.status is Status.INTERVIEW
    assert app.follow_up_date == date(2026, 3, 25)
    assert app.location == "Pune"
    assert app.created_at == NOW and app.updated_at == later


def test_update_can_clear_a_value(conn):
    app_id = add(conn, follow_up_date=date(2026, 3, 16))
    with conn:
        db.update_application(conn, app_id, {"follow_up_date": None}, now=NOW)
    assert db.get_application(conn, app_id).follow_up_date is None


def test_update_unknown_id_returns_false(conn):
    with conn:
        assert db.update_application(conn, 42, {"notes": "x"}, now=NOW) is False


def test_update_refuses_columns_outside_the_list(conn):
    app_id = add(conn)
    with pytest.raises(ValueError, match="id"):
        db.update_application(conn, app_id, {"id": 7}, now=NOW)


# --- delete -----------------------------------------------------------------


def test_delete_removes_application_and_its_history(conn):
    keep = add(conn, company="Keep Example Co")
    gone = add(conn, company="Gone Example Co")
    with conn:
        for app_id in (keep, gone):
            db.insert_status_change(
                conn, app_id, old_status=None, new_status=Status.APPLIED, changed_on=date(2026, 3, 2)
            )
        assert db.delete_application(conn, gone)

    assert db.get_application(conn, gone) is None
    assert db.get_history(conn, gone) == []
    assert len(db.get_history(conn, keep)) == 1
    assert conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0] == 1


def test_delete_unknown_id_returns_false(conn):
    with conn:
        assert db.delete_application(conn, 5) is False


def test_ids_are_not_reused_after_delete(conn):
    first = add(conn)
    second = add(conn)
    with conn:
        db.delete_application(conn, second)
    third = add(conn)
    assert (first, second, third) == (1, 2, 3)


def test_history_needs_an_existing_application(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_status_change(
            conn, 123, old_status=None, new_status=Status.APPLIED, changed_on=date(2026, 3, 2)
        )


# --- history ----------------------------------------------------------------


def test_history_is_returned_in_recorded_order(conn):
    app_id = add(conn)
    with conn:
        db.insert_status_change(
            conn, app_id, old_status=None, new_status=Status.APPLIED, changed_on=date(2026, 3, 2)
        )
        db.insert_status_change(
            conn,
            app_id,
            old_status=Status.APPLIED,
            new_status=Status.SCREENING,
            changed_on=date(2026, 3, 9),
            note="phone call",
        )
    first, second = db.get_history(conn, app_id)
    assert first.old_status is None and first.new_status is Status.APPLIED
    assert second.old_status is Status.APPLIED and second.new_status is Status.SCREENING
    assert second.changed_on == date(2026, 3, 9)
    assert second.note == "phone call"
    assert second.application_id == app_id


# --- queries ----------------------------------------------------------------


@pytest.fixture
def populated(conn):
    add(conn, company="Alpha Example", role="Data Analyst", date_applied=date(2026, 2, 10),
        status=Status.REJECTED, location="Mumbai", source="job board")
    add(conn, company="beta example", role="ML Intern", date_applied=date(2026, 3, 1),
        status=Status.APPLIED, location="Remote", notes="100% remote_team",
        follow_up_date=date(2026, 3, 15))
    add(conn, company="Gamma Example", role="Backend Developer", date_applied=date(2026, 3, 12),
        status=Status.INTERVIEW, location="Pune", follow_up_date=date(2026, 3, 22))
    add(conn, company="Delta Example", role="Python Developer", date_applied=None,
        status=Status.SAVED, source="referral")
    return conn


def ids(apps):
    return [app.id for app in apps]


def test_query_without_filters_returns_all_by_id(populated):
    assert ids(db.query_applications(populated)) == [1, 2, 3, 4]


def test_query_by_status(populated):
    assert ids(db.query_applications(populated, statuses=[Status.APPLIED])) == [2]
    assert ids(
        db.query_applications(populated, statuses=[Status.SAVED, Status.REJECTED])
    ) == [1, 4]


def test_query_by_company_substring_ignores_case(populated):
    assert ids(db.query_applications(populated, company="BETA")) == [2]
    assert ids(db.query_applications(populated, company="example")) == [1, 2, 3, 4]


def test_query_by_date_range_is_inclusive_and_skips_missing_dates(populated):
    found = db.query_applications(populated, date_from=date(2026, 3, 1), date_to=date(2026, 3, 12))
    assert ids(found) == [2, 3]
    assert ids(db.query_applications(populated, date_from=date(2026, 3, 2))) == [3]
    assert ids(db.query_applications(populated, date_to=date(2026, 2, 28))) == [1]


def test_query_search_looks_in_several_columns(populated):
    assert ids(db.query_applications(populated, search="developer")) == [3, 4]
    assert ids(db.query_applications(populated, search="pune")) == [3]
    assert ids(db.query_applications(populated, search="referral")) == [4]
    assert ids(db.query_applications(populated, search="remote")) == [2]
    assert db.query_applications(populated, search="nothing like this") == []


def test_query_search_treats_wildcards_as_plain_text(populated):
    assert ids(db.query_applications(populated, search="100%")) == [2]
    assert ids(db.query_applications(populated, search="remote_team")) == [2]
    assert db.query_applications(populated, search="a%e") == []
    assert db.query_applications(populated, search="_") == [db.get_application(populated, 2)]


def test_query_filters_combine_with_and(populated):
    found = db.query_applications(
        populated, statuses=[Status.APPLIED, Status.INTERVIEW], search="developer"
    )
    assert ids(found) == [3]


def test_query_follow_up_until(populated):
    assert ids(db.query_applications(populated, follow_up_until=date(2026, 3, 15))) == [2]
    assert ids(db.query_applications(populated, follow_up_until=date(2026, 3, 22))) == [2, 3]
    assert db.query_applications(populated, follow_up_until=date(2026, 3, 14)) == []


def test_query_sorting(populated):
    assert ids(db.query_applications(populated, sort="company")) == [1, 2, 4, 3]
    assert ids(db.query_applications(populated, sort="company", descending=True)) == [3, 4, 2, 1]
    # Applications without a date come last in both directions.
    assert ids(db.query_applications(populated, sort="applied")) == [1, 2, 3, 4]
    assert ids(db.query_applications(populated, sort="applied", descending=True)) == [3, 2, 1, 4]
    assert ids(db.query_applications(populated, sort="followup")) == [2, 3, 1, 4]
    # Pipeline order, not alphabetical: saved, applied, interview, rejected.
    assert ids(db.query_applications(populated, sort="status")) == [4, 2, 3, 1]
    assert ids(db.query_applications(populated, sort="id", descending=True)) == [4, 3, 2, 1]


def test_query_limit(populated):
    assert ids(db.query_applications(populated, limit=2)) == [1, 2]


def test_query_unknown_sort_key_is_refused(populated):
    with pytest.raises(ValueError):
        db.query_applications(populated, sort="id; DROP TABLE applications")


def test_find_duplicate(populated):
    assert db.find_duplicate(populated, "ALPHA EXAMPLE", "data analyst", date(2026, 2, 10)) == 1
    assert db.find_duplicate(populated, "Alpha Example", "Data Analyst", date(2026, 2, 11)) is None
    assert db.find_duplicate(populated, "Alpha Example", "Data Analyst", None) is None
    assert db.find_duplicate(populated, "Delta Example", "Python Developer", None) == 4
