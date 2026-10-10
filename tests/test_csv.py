"""CSV export and import against temporary files and a temporary database."""

import csv
from dataclasses import replace
from datetime import date

import pytest

from jobtracker import db
from jobtracker.csv_io import COLUMNS, export_csv, import_csv, read_csv
from jobtracker.errors import CsvImportError, ValidationError
from jobtracker.models import Status
from jobtracker.service import IMPORT_NOTE, TrackerService

from .conftest import TODAY

S = Status


@pytest.fixture
def filled(svc):
    """One application in every status, with a mix of optional fields."""
    def add(company, applied_on, **kwargs):
        return svc.add(
            company=company, role="Developer", today=TODAY, date_applied=applied_on, **kwargs
        )

    def move(app, status, on, **kwargs):
        return svc.change_status(app.id, status, on=on, today=TODAY, **kwargs)

    svc.add(company="Saved Example", role="ML Intern", today=TODAY, status=S.SAVED,
            link="https://example.com/jobs/1")
    waiting = add("Applied Example", date(2026, 3, 2), location="Pune", source="job board",
                  notes='says "urgent", twice\nsecond line')
    svc.set_follow_up(waiting.id, date(2026, 3, 16))
    move(add("Screening Example", date(2026, 3, 1)), S.SCREENING, date(2026, 3, 6))
    interview = add("Interview Example", date(2026, 3, 2), location="Mumbai, India")
    move(interview, S.SCREENING, date(2026, 3, 5))
    move(interview, S.INTERVIEW, date(2026, 3, 12))
    move(add("Offer Example", date(2026, 2, 20)), S.OFFER, date(2026, 3, 10))
    move(add("Rejected Example", date(2026, 3, 3)), S.REJECTED, date(2026, 3, 18))
    left = add("Withdrawn Example", date(2026, 3, 4))
    move(left, S.INTERVIEW, date(2026, 3, 9))
    move(left, S.WITHDRAWN, date(2026, 3, 11))
    dropped = svc.add(company="Dropped Example", role="Developer", today=TODAY, status=S.SAVED)
    move(dropped, S.WITHDRAWN, TODAY)
    return svc


@pytest.fixture
def other(tmp_path):
    """A second, empty database to import into."""
    connection = db.connect(tmp_path / "other.db")
    yield TrackerService(connection)
    connection.close()


def write(tmp_path, text, name="in.csv", encoding="utf-8"):
    path = tmp_path / name
    path.write_text(text, encoding=encoding, newline="")
    return path


def comparable(app):
    """An application without the fields that legitimately differ after an import."""
    return replace(app, id=0, created_at="", updated_at="")


# --- export -----------------------------------------------------------------


def test_export_writes_header_and_one_row_per_application(filled, tmp_path):
    path = tmp_path / "out.csv"
    assert export_csv(filled, path) == 8
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == list(COLUMNS)
    assert len(rows) == 9
    assert [row[0] for row in rows[1:]] == [str(n) for n in range(1, 9)]


def test_export_formats_dates_status_and_blanks(filled, tmp_path):
    path = tmp_path / "out.csv"
    export_csv(filled, path)
    with path.open(newline="", encoding="utf-8") as handle:
        by_company = {row["company"]: row for row in csv.DictReader(handle)}

    saved = by_company["Saved Example"]
    assert saved["status"] == "saved"
    assert saved["date_applied"] == "" and saved["location"] == ""

    interview = by_company["Interview Example"]
    assert interview["status"] == "interview"
    assert interview["date_applied"] == "2026-03-02"
    assert interview["first_response_date"] == "2026-03-05"
    assert interview["location"] == "Mumbai, India"

    waiting = by_company["Applied Example"]
    assert waiting["follow_up_date"] == "2026-03-16"
    assert waiting["notes"] == 'says "urgent", twice\nsecond line'


def test_export_of_empty_database_writes_only_the_header(svc, tmp_path):
    path = tmp_path / "out.csv"
    assert export_csv(svc, path) == 0
    assert path.read_text(encoding="utf-8").strip() == ",".join(COLUMNS)


def test_export_does_not_overwrite_unless_asked(filled, tmp_path):
    path = tmp_path / "out.csv"
    path.write_text("keep me", encoding="utf-8")
    with pytest.raises(ValidationError, match="already exists"):
        export_csv(filled, path)
    assert path.read_text(encoding="utf-8") == "keep me"
    assert export_csv(filled, path, overwrite=True) == 8


def test_export_to_a_folder_or_missing_folder_is_refused(filled, tmp_path):
    with pytest.raises(ValidationError, match="is a folder"):
        export_csv(filled, tmp_path)
    with pytest.raises(ValidationError, match="Cannot write"):
        export_csv(filled, tmp_path / "no-such-folder" / "out.csv")


# --- round trip -------------------------------------------------------------


def test_export_then_import_gives_the_same_applications(filled, other, tmp_path):
    path = tmp_path / "out.csv"
    export_csv(filled, path)
    result = import_csv(other, path, today=TODAY)

    assert (result.rows_read, result.imported, result.skipped_duplicates) == (8, 8, 0)
    assert [comparable(app) for app in other.find()] == [
        comparable(app) for app in filled.find()
    ]


def test_summary_survives_the_round_trip(filled, other, tmp_path):
    path = tmp_path / "out.csv"
    export_csv(filled, path)
    import_csv(other, path, today=TODAY)
    assert other.summary() == filled.summary()


def test_imported_application_starts_with_one_history_entry(filled, other, tmp_path):
    path = tmp_path / "out.csv"
    export_csv(filled, path)
    import_csv(other, path, today=TODAY)
    interview = other.find(company="Interview Example")[0]
    (entry,) = other.history(interview.id)
    assert entry.old_status is None and entry.new_status is S.INTERVIEW
    assert entry.changed_on == TODAY
    assert entry.note == IMPORT_NOTE


def test_importing_the_same_file_twice_adds_nothing(filled, other, tmp_path):
    path = tmp_path / "out.csv"
    export_csv(filled, path)
    import_csv(other, path, today=TODAY)
    second = import_csv(other, path, today=TODAY)
    assert (second.imported, second.skipped_duplicates) == (0, 8)
    assert len(other.find()) == 8


def test_importing_into_the_source_database_skips_everything(filled, tmp_path):
    path = tmp_path / "out.csv"
    export_csv(filled, path)
    result = import_csv(filled, path, today=TODAY)
    assert (result.imported, result.skipped_duplicates) == (0, 8)


# --- import: accepted input -------------------------------------------------


def test_minimal_file_needs_only_company_and_role(other, tmp_path):
    path = write(tmp_path, "company,role,date_applied\nNorthwind Example Ltd,Developer,2026-03-02\n")
    assert import_csv(other, path, today=TODAY).imported == 1
    (app,) = other.find()
    assert app.status is S.APPLIED
    assert app.date_applied == date(2026, 3, 2)
    assert app.link is None and app.notes is None


def test_id_column_is_ignored_and_new_ids_assigned(other, tmp_path):
    path = write(
        tmp_path,
        "id,company,role,status\n41,Alpha Example,Developer,saved\n97,Beta Example,Developer,saved\n",
    )
    import_csv(other, path, today=TODAY)
    assert [(app.id, app.company) for app in other.find()] == [
        (1, "Alpha Example"), (2, "Beta Example"),
    ]


def test_header_names_are_forgiving_about_case_and_spaces(other, tmp_path):
    path = write(tmp_path, "Company, Role ,Date Applied,STATUS\nAlpha Example,Dev,2026-03-02,Applied\n")
    import_csv(other, path, today=TODAY)
    assert other.find()[0].date_applied == date(2026, 3, 2)


def test_file_saved_with_a_byte_order_mark_is_read(other, tmp_path):
    path = write(tmp_path, "company,role,status\nAlpha Example,Dev,saved\n", encoding="utf-8-sig")
    assert import_csv(other, path, today=TODAY).imported == 1
    assert other.find()[0].company == "Alpha Example"


def test_blank_lines_and_short_rows_are_tolerated(other, tmp_path):
    path = write(
        tmp_path,
        "company,role,status,notes\nAlpha Example,Dev,saved\n\n,,,\nBeta Example,Dev,saved,hello\n",
    )
    result = import_csv(other, path, today=TODAY)
    assert result.rows_read == 2 and result.imported == 2


def test_duplicate_rows_inside_one_file_are_skipped(other, tmp_path):
    path = write(
        tmp_path,
        "company,role,date_applied\n"
        "Alpha Example,Dev,2026-03-02\n"
        "alpha example,DEV,2026-03-02\n"
        "Alpha Example,Dev,2026-03-03\n",
    )
    result = import_csv(other, path, today=TODAY)
    assert (result.rows_read, result.imported, result.skipped_duplicates) == (3, 2, 1)


# --- import: refused input --------------------------------------------------


def expect_problems(service, path):
    with pytest.raises(CsvImportError) as excinfo:
        import_csv(service, path, today=TODAY)
    return excinfo.value.problems


def test_invalid_rows_are_listed_by_line_and_nothing_is_imported(other, tmp_path):
    path = write(
        tmp_path,
        "company,role,status,date_applied\n"          # line 1
        "Alpha Example,Dev,applied,2026-03-02\n"       # line 2, fine
        "Beta Example,Dev,applied,02/03/2026\n"        # line 3, date format
        ",Dev,applied,2026-03-02\n"                    # line 4, no company
        "Gamma Example,Dev,hired,2026-03-02\n"         # line 5, status
        "Delta Example,Dev,interview,\n"               # line 6, needs applied date
        "Epsilon Example,Dev,applied,2026-02-30\n",    # line 7, impossible date
    )
    problems = expect_problems(other, path)
    assert len(problems) == 5
    assert problems[0].startswith("line 3:") and "02/03/2026" in problems[0]
    assert problems[1].startswith("line 4:") and "company cannot be empty" in problems[1]
    assert problems[2].startswith("line 5:") and "Unknown status 'hired'" in problems[2]
    assert problems[3].startswith("line 6:") and "needs an applied date" in problems[3]
    assert problems[4].startswith("line 7:") and "does not exist" in problems[4]
    assert other.find() == []


def test_one_row_can_report_several_problems(other, tmp_path):
    path = write(
        tmp_path,
        "company,role,status,date_applied,follow_up_date\nAlpha Example,Dev,nope,soon,later\n",
    )
    problems = expect_problems(other, path)
    assert len(problems) == 3
    assert all(problem.startswith("line 2:") for problem in problems)


def test_error_message_counts_and_lists_the_problems(other, tmp_path):
    path = write(tmp_path, "company,role,status\nAlpha Example,Dev,nope\n")
    with pytest.raises(CsvImportError) as excinfo:
        import_csv(other, path, today=TODAY)
    message = str(excinfo.value)
    assert message.startswith("CSV not imported, 1 problem found:")
    assert "\n  line 2: Unknown status 'nope'" in message


def test_future_applied_date_is_refused(other, tmp_path):
    path = write(tmp_path, "company,role,date_applied\nAlpha Example,Dev,2026-03-21\n")
    (problem,) = expect_problems(other, path)
    assert "in the future" in problem


def test_missing_required_column(other, tmp_path):
    path = write(tmp_path, "company,status\nAlpha Example,saved\n")
    assert expect_problems(other, path) == ["line 1: the required column 'role' is missing"]


def test_unknown_and_repeated_columns(other, tmp_path):
    path = write(tmp_path, "company,role,salary,role\nAlpha Example,Dev,1,Dev\n")
    problems = expect_problems(other, path)
    assert any("unknown column 'salary'" in problem for problem in problems)
    assert any("'role' appears more than once" in problem for problem in problems)


def test_row_with_too_many_values(other, tmp_path):
    path = write(tmp_path, "company,role\nAlpha Example,Dev,extra\n")
    assert expect_problems(other, path) == ["line 2: 3 values but only 2 columns"]


def test_empty_file(other, tmp_path):
    (problem,) = expect_problems(other, write(tmp_path, ""))
    assert "empty" in problem


def test_header_only_file_imports_nothing(other, tmp_path):
    result = import_csv(other, write(tmp_path, "company,role\n"), today=TODAY)
    assert (result.rows_read, result.imported) == (0, 0)


def test_missing_file(other, tmp_path):
    with pytest.raises(ValidationError, match="File not found"):
        import_csv(other, tmp_path / "nope.csv", today=TODAY)


def test_file_that_is_not_utf8(other, tmp_path):
    path = tmp_path / "latin.csv"
    path.write_bytes("company,role\nCaf\xe9 Example,Dev\n".encode("latin-1"))
    with pytest.raises(ValidationError, match="not UTF-8"):
        import_csv(other, path, today=TODAY)


# --- dry run ----------------------------------------------------------------


def test_dry_run_reports_counts_but_stores_nothing(filled, other, tmp_path):
    path = tmp_path / "out.csv"
    export_csv(filled, path)
    result = import_csv(other, path, today=TODAY, dry_run=True)
    assert result.dry_run
    assert (result.imported, result.skipped_duplicates) == (8, 0)
    assert other.find() == []
    # The ids were not used up either.
    import_csv(other, path, today=TODAY)
    assert [app.id for app in other.find()] == list(range(1, 9))


def test_dry_run_still_reports_invalid_rows(other, tmp_path):
    path = write(tmp_path, "company,role,status\nAlpha Example,Dev,nope\n")
    with pytest.raises(CsvImportError):
        import_csv(other, path, today=TODAY, dry_run=True)


def test_read_csv_alone_never_writes(other, tmp_path):
    path = write(tmp_path, "company,role,status\nAlpha Example,Dev,saved\n")
    rows = read_csv(other, path, today=TODAY)
    assert [row.company for row in rows] == ["Alpha Example"]
    assert other.find() == []
