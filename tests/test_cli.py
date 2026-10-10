"""The CLI layer through Typer's test runner, on a temporary database with a fixed date."""

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobtracker import __version__, db
from jobtracker.cli import app, default_db_path
from jobtracker.models import Status
from jobtracker.service import TrackerService

REPO_ROOT = Path(__file__).resolve().parent.parent
TODAY = "2026-03-20"
runner = CliRunner()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "cli.db"


@pytest.fixture
def run(db_path):
    """Run one command line. Returns Typer's result (exit_code, stdout, stderr, output)."""

    def invoke(*args, input=None):
        result = runner.invoke(
            app,
            ["--db", str(db_path), *map(str, args)],
            input=input,
            env={"JOBTRACK_TODAY": TODAY},
        )
        # Whatever happens, the user must never be shown a Python traceback.
        assert "Traceback" not in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)
        return result

    return invoke


@pytest.fixture
def stored(db_path):
    """Read the database behind the CLI's back, to check what was really stored."""

    def read():
        conn = db.connect(db_path)
        try:
            return TrackerService(conn).find()
        finally:
            conn.close()

    return read


def ok(result):
    assert result.exit_code == 0, result.output
    return result.stdout


def refused(result, *fragments):
    """An expected error: exit code 1, one 'Error:' message on stderr, nothing on stdout."""
    assert result.exit_code == 1, result.output
    assert result.stderr.startswith("Error: ")
    assert result.stdout == ""
    for fragment in fragments:
        assert fragment in result.stderr
    return result.stderr


@pytest.fixture
def two(run):
    ok(run("add", "-c", "Northwind Example Ltd", "-r", "Data Analyst", "-d", "2026-03-02",
           "--location", "Pune", "--source", "referral"))
    ok(run("add", "--company", "Bluefield Example Co", "--role", "ML Intern", "--status", "saved"))


# --- help and version -------------------------------------------------------


def test_no_arguments_shows_help():
    result = runner.invoke(app, [])
    assert "Usage: " in result.output
    for command in ("add", "list", "show", "status", "followup", "due", "edit", "delete",
                    "summary", "export", "import"):
        assert command in result.output


@pytest.mark.parametrize(
    "command",
    ["add", "list", "show", "status", "followup", "due", "edit", "delete", "summary", "export",
     "import"],
)
def test_every_command_has_help_text(run, command):
    out = ok(run(command, "--help"))
    assert out.startswith(f"Usage: ")
    assert command in out.splitlines()[0]
    # A description line follows the usage line.
    assert len([line for line in out.splitlines() if line.strip()]) >= 3


def test_help_is_plain_text(run):
    out = ok(run("--help"))
    assert out.isascii()
    assert "saved, applied, screening, interview, offer, rejected, withdrawn" in out


def test_version(run):
    assert ok(run("--version")).strip() == f"jobtrack {__version__}"


# --- add --------------------------------------------------------------------


def test_add_defaults_to_applied_today(run, stored):
    out = ok(run("add", "--company", "Northwind Example Ltd", "--role", "Data Analyst"))
    assert out == (
        "Added application 1: Data Analyst at Northwind Example Ltd (applied on 2026-03-20).\n"
    )
    (app_,) = stored()
    assert app_.status is Status.APPLIED and app_.date_applied.isoformat() == TODAY


def test_add_with_every_option(run, stored):
    ok(run("add", "-c", "Northwind Example Ltd", "-r", "Data Analyst",
           "--link", "https://example.com/jobs/1", "--location", "Pune",
           "--source", "referral", "--notes", "synthetic", "--date-applied", "2026-03-02"))
    (app_,) = stored()
    assert (app_.link, app_.location, app_.source, app_.notes) == (
        "https://example.com/jobs/1", "Pune", "referral", "synthetic",
    )
    assert app_.date_applied.isoformat() == "2026-03-02"


def test_add_saved(run, stored):
    out = ok(run("add", "-c", "Bluefield Example Co", "-r", "ML Intern", "--status", "saved"))
    assert "(saved, not applied yet)" in out
    assert stored()[0].date_applied is None


def test_add_invalid_date(run, stored):
    refused(run("add", "-c", "X", "-r", "Y", "-d", "02/03/2026"),
            "Invalid applied date '02/03/2026'", "YYYY-MM-DD")
    assert stored() == []


def test_add_impossible_date(run):
    refused(run("add", "-c", "X", "-r", "Y", "-d", "2026-02-30"), "does not exist")


def test_add_future_date(run):
    refused(run("add", "-c", "X", "-r", "Y", "-d", "2026-03-21"), "in the future")


def test_add_blank_company(run):
    refused(run("add", "-c", "  ", "-r", "Y"), "company cannot be empty")


def test_add_unknown_status(run):
    refused(run("add", "-c", "X", "-r", "Y", "--status", "hired"), "Unknown status 'hired'")


def test_add_missing_required_option_is_a_usage_error(run):
    result = run("add", "--company", "X")
    assert result.exit_code == 2
    assert "--role" in result.stderr


# --- list -------------------------------------------------------------------


def test_list_empty_database(run):
    assert "No applications yet" in ok(run("list"))


def test_list_shows_a_table(run, two):
    out = ok(run("list"))
    lines = out.splitlines()
    assert lines[0].split() == ["ID", "Company", "Role", "Status", "Applied", "Follow-up",
                                "Location"]
    assert "Northwind Example Ltd" in lines[2] and "2026-03-02" in lines[2] and "Pune" in lines[2]
    assert "Bluefield Example Co" in lines[3] and "saved" in lines[3]
    assert lines[-1] == "2 applications"


def test_list_filters(run, two):
    assert "1 application\n" in ok(run("list", "--status", "saved"))
    assert "Bluefield" not in ok(run("list", "--status", "applied"))
    assert "2 applications" in ok(run("list", "-s", "saved,applied"))
    assert "2 applications" in ok(run("list", "-s", "saved", "-s", "applied"))
    assert "Northwind" not in ok(run("list", "--company", "blue"))
    assert "1 application\n" in ok(run("list", "--from", "2026-03-01", "--to", "2026-03-05"))
    assert "1 application\n" in ok(run("list", "--search", "referral"))
    assert ok(run("list", "--search", "nothing")) == "No applications match.\n"


def test_list_sort_and_limit(run, two):
    out = ok(run("list", "--sort", "company", "--limit", "1"))
    assert "Bluefield" in out and "Northwind" not in out
    out = ok(run("list", "--sort", "id", "--desc"))
    assert out.index("Bluefield") < out.index("Northwind")


def test_list_errors(run, two):
    refused(run("list", "--status", "hired"), "Unknown status")
    refused(run("list", "--from", "March"), "Invalid --from date 'March'")
    refused(run("list", "--from", "2026-03-10", "--to", "2026-03-01"), "backwards")
    refused(run("list", "--sort", "salary"), "Unknown sort key 'salary'")
    refused(run("list", "--limit", "0"), "limit")


def test_long_text_is_shortened_in_the_table_only(run, stored):
    name = "A Very Long Company Name That Goes On And On Example Ltd"
    ok(run("add", "-c", name, "-r", "Developer"))
    table = ok(run("list"))
    assert name not in table and "..." in table
    assert name in ok(run("show", 1))


# --- show -------------------------------------------------------------------


def test_show_details_and_history(run, two):
    ok(run("status", 1, "screening", "--date", "2026-03-09", "--note", "recruiter call"))
    out = ok(run("show", 1))
    assert "Application 1" in out
    assert "Company:         Northwind Example Ltd" in out
    assert "Status:          screening" in out
    assert "First response:  2026-03-09" in out
    assert "2026-03-02  added as applied" in out
    assert "2026-03-09  applied -> screening  (recruiter call)" in out


def test_show_unknown_id(run):
    refused(run("show", 5), "No application with id 5")


def test_show_id_must_be_a_number(run):
    result = run("show", "abc")
    assert result.exit_code == 2
    assert "not a valid int" in result.stderr


# --- status -----------------------------------------------------------------


def test_status_move(run, two, stored):
    out = ok(run("status", 1, "interview", "--date", "2026-03-10"))
    assert "Application 1: applied -> interview on 2026-03-10." in out
    assert "First response recorded, 8 days after applying." in out
    assert stored()[0].status is Status.INTERVIEW


def test_status_date_defaults_to_today(run, two, stored):
    assert "saved -> applied on 2026-03-20" in ok(run("status", 2, "applied"))
    assert stored()[1].date_applied.isoformat() == TODAY


def test_status_name_is_not_case_sensitive(run, two):
    ok(run("status", 1, "Rejected"))


def test_status_refused_move_explains_and_changes_nothing(run, two, stored):
    ok(run("status", 1, "interview"))
    message = refused(run("status", 1, "applied"), "Cannot move from 'interview' to 'applied'",
                      "--force")
    assert "offer, rejected, withdrawn" in message
    assert stored()[0].status is Status.INTERVIEW


def test_status_force(run, two, stored):
    ok(run("status", 1, "rejected"))
    refused(run("status", 1, "interview"), "closed status")
    ok(run("status", 1, "interview", "--force"))
    assert stored()[0].status is Status.INTERVIEW


def test_status_errors(run, two):
    refused(run("status", 1, "intervew"), "Did you mean 'interview'?")
    refused(run("status", 1, "applied"), "already 'applied'")
    refused(run("status", 9, "applied"), "No application with id 9")
    refused(run("status", 1, "screening", "--date", "2026-03-01"), "before the applied date")
    refused(run("status", 1, "screening", "--date", "2026-04-01"), "in the future")
    refused(run("status", 1, "screening", "--date", "yesterday"), "Invalid status change date")


# --- followup and due -------------------------------------------------------


def test_followup_set_relative_and_clear(run, two, stored):
    assert "follow-up set for 2026-03-18" in ok(run("followup", 1, "2026-03-18"))
    assert "follow-up set for 2026-03-27" in ok(run("followup", 1, "+7d"))
    assert stored()[0].follow_up_date.isoformat() == "2026-03-27"
    assert "follow-up date removed" in ok(run("followup", 1, "--clear"))
    assert stored()[0].follow_up_date is None


def test_followup_errors(run, two):
    refused(run("followup", 1), "Give a follow-up date")
    refused(run("followup", 1, "2026-03-18", "--clear"), "not both")
    refused(run("followup", 1, "next week"), "Invalid follow-up date 'next week'", "+7d")
    refused(run("followup", 7, "today"), "No application with id 7")


def test_followup_on_closed_application_warns(run, two):
    ok(run("status", 1, "rejected"))
    assert "will not appear in 'jobtrack due'" in ok(run("followup", 1, "today"))


def test_due_lists_overdue_and_today_only(run, two):
    ok(run("add", "-c", "Later Example", "-r", "Developer"))
    ok(run("followup", 1, "2026-03-17"))
    ok(run("followup", 2, "today"))
    ok(run("followup", 3, "+2d"))
    out = ok(run("due"))
    assert "Follow-ups as of 2026-03-20:" in out
    assert "overdue by 3 days" in out and "due today" in out
    assert "Later Example" not in out
    assert out.index("Northwind") < out.index("Bluefield")
    assert out.splitlines()[-1] == "1 overdue, 1 due today"

    out = ok(run("due", "--within", 2))
    assert "in 2 days" in out
    assert out.splitlines()[-1] == "1 overdue, 1 due today, 1 upcoming"


def test_due_when_nothing_is_due(run, two):
    assert ok(run("due")) == "No follow-ups due as of 2026-03-20.\n"
    assert "or in the next 5 days" in ok(run("due", "--within", 5))


def test_due_skips_closed_applications(run, two):
    ok(run("followup", 1, "2026-03-17"))
    ok(run("status", 1, "withdrawn"))
    assert "No follow-ups due" in ok(run("due"))


def test_due_negative_within(run):
    refused(run("due", "--within", "-1"), "cannot be negative")


# --- edit -------------------------------------------------------------------


def test_edit_changes_given_fields(run, two, stored):
    out = ok(run("edit", 1, "--role", "Senior Data Analyst", "--notes", "updated"))
    assert "Updated application 1: role, notes." in out
    app_ = stored()[0]
    assert (app_.role, app_.notes, app_.location) == ("Senior Data Analyst", "updated", "Pune")


def test_edit_empty_value_clears_optional_field(run, two, stored):
    ok(run("edit", 1, "--location", ""))
    assert stored()[0].location is None


def test_edit_applied_date(run, two, stored):
    ok(run("edit", 1, "-d", "2026-03-01"))
    assert stored()[0].date_applied.isoformat() == "2026-03-01"


def test_edit_errors(run, two):
    refused(run("edit", 1), "Nothing to change")
    refused(run("edit", 1, "--company", ""), "company cannot be empty")
    refused(run("edit", 1, "-d", "soon"), "Invalid applied date 'soon'")
    refused(run("edit", 2, "-d", "2026-03-01"), "has no applied date")
    refused(run("edit", 8, "--notes", "x"), "No application with id 8")


# --- delete -----------------------------------------------------------------


def test_delete_asks_and_deletes_on_yes(run, two, stored):
    result = run("delete", 1, input="y\n")
    out = ok(result)
    assert "Northwind Example Ltd" in out
    assert "Delete this application? This cannot be undone. [y/N]" in out
    assert "Deleted application 1: Data Analyst at Northwind Example Ltd." in out
    assert [app_.id for app_ in stored()] == [2]


@pytest.mark.parametrize("answer", ["n\n", "\n", "no\n"])
def test_delete_keeps_the_application_unless_confirmed(run, two, stored, answer):
    out = ok(run("delete", 1, input=answer))
    assert "Nothing deleted." in out
    assert len(stored()) == 2


def test_delete_without_an_answer_aborts_and_keeps_it(run, two, stored):
    result = run("delete", 1, input="")
    assert result.exit_code == 1
    assert len(stored()) == 2


def test_delete_yes_flag_skips_the_question(run, two, stored):
    out = ok(run("delete", 1, "--yes"))
    assert "[y/N]" not in out
    assert out == "Deleted application 1: Data Analyst at Northwind Example Ltd.\n"
    assert len(stored()) == 1


def test_delete_unknown_id_fails_before_asking(run, two):
    message = refused(run("delete", 9), "No application with id 9")
    assert "[y/N]" not in message


# --- summary ----------------------------------------------------------------


def test_summary_on_empty_database(run):
    out = ok(run("summary"))
    assert "Applications: 0" in out
    assert "Response rate: n/a" in out
    assert "first response: n/a" in out


def test_summary_numbers(run, two):
    ok(run("add", "-c", "Gamma Example", "-r", "Developer", "-d", "2026-03-05"))
    ok(run("status", 1, "screening", "--date", "2026-03-06"))  # 4 days after Mar 02
    out = ok(run("summary"))
    assert "Applications: 3" in out
    rows = {line.split()[0]: line.split()[1] for line in out.splitlines()
            if line.split() and line.split()[0] in {s.value for s in Status}}
    assert rows == {"saved": "1", "applied": "1", "screening": "1", "interview": "0",
                    "offer": "0", "rejected": "0", "withdrawn": "0"}
    assert "Response rate: 50.0% (1 of 2 applied got a response)" in out
    assert "Average days from applied to first response: 4.0 (based on 1 application)" in out


def test_summary_date_range(run, two):
    out = ok(run("summary", "--from", "2026-03-03"))
    assert "Applications: 0" in out
    assert "Only applications applied on or after 2026-03-03 are counted." in out
    refused(run("summary", "--from", "2026-03-10", "--to", "2026-03-01"), "backwards")


# --- export / import --------------------------------------------------------


def test_export_and_import_into_another_database(run, two, tmp_path):
    out_file = tmp_path / "apps.csv"
    assert f"Exported 2 applications to {out_file}." in ok(run("export", out_file))
    with out_file.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["company"] for row in rows] == ["Northwind Example Ltd", "Bluefield Example Co"]

    refused(run("export", out_file), "already exists", "--overwrite")
    ok(run("export", out_file, "--overwrite"))

    other = tmp_path / "other.db"

    def run_other(*args):
        return runner.invoke(app, ["--db", str(other), *map(str, args)],
                             env={"JOBTRACK_TODAY": TODAY})

    dry = run_other("import", out_file, "--dry-run")
    assert "Dry run: 2 applications would be imported, 0 duplicates skipped." in dry.stdout
    assert "No applications yet" in run_other("list").stdout

    assert "Imported 2 applications" in run_other("import", out_file).stdout
    assert "2 applications" in run_other("list").stdout
    again = run_other("import", out_file).stdout
    assert "Imported 0 applications" in again and "skipped 2 duplicates" in again


def test_import_invalid_file_lists_problems(run, tmp_path, stored):
    bad = tmp_path / "bad.csv"
    bad.write_text("company,role,status\nAlpha Example,Dev,nope\n,Dev,saved\n", encoding="utf-8")
    message = refused(run("import", bad), "CSV not imported, 2 problems found:")
    assert "line 2: Unknown status 'nope'" in message
    assert "line 3: The company cannot be empty." in message
    assert stored() == []


def test_import_missing_file(run, tmp_path):
    refused(run("import", tmp_path / "missing.csv"), "File not found")


# --- database handling ------------------------------------------------------


def test_file_that_is_not_a_database(run, db_path):
    db_path.write_text("this is not a database, just text " * 20, encoding="utf-8")
    refused(run("list"), "cannot use the database")


def test_database_path_from_environment(tmp_path):
    env_db = tmp_path / "from-env.db"
    env = {"JOBTRACK_DB": str(env_db), "JOBTRACK_TODAY": TODAY}
    result = runner.invoke(app, ["add", "-c", "Env Example", "-r", "Developer"], env=env)
    assert result.exit_code == 0, result.output
    assert env_db.exists()
    assert "Env Example" in runner.invoke(app, ["list"], env=env).stdout


def test_database_folder_is_created(tmp_path):
    nested = tmp_path / "a" / "b" / "jobs.db"
    result = runner.invoke(app, ["--db", str(nested), "list"], env={"JOBTRACK_TODAY": TODAY})
    assert result.exit_code == 0, result.output
    assert nested.exists()


def test_default_database_lives_in_the_home_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert default_db_path() == tmp_path / ".jobtrack" / "jobtrack.db"


def test_bad_today_override_is_reported(db_path):
    result = runner.invoke(app, ["--db", str(db_path), "list"], env={"JOBTRACK_TODAY": "soon"})
    assert result.exit_code == 1
    assert "JOBTRACK_TODAY" in result.stderr
    assert "Traceback" not in result.output


def test_non_ascii_text_is_stored_and_listed(run, stored):
    ok(run("add", "-c", "Société Exemple 株式会社", "-r", "Développeur"))
    assert stored()[0].company == "Société Exemple 株式会社"
    ok(run("list"))
    ok(run("show", 1))


def test_runs_as_a_module_when_the_output_cannot_carry_every_character(db_path, stored):
    """`python -m jobtracker` in a real process whose output is cp1252, the usual
    encoding of redirected output on Windows. Characters outside it print as '?'."""
    env = {
        **os.environ,
        "JOBTRACK_TODAY": TODAY,
        "PYTHONIOENCODING": "cp1252",
        "PYTHONUTF8": "0",
        "PYTHONPATH": str(REPO_ROOT),
    }

    def module(*args):
        return subprocess.run(
            [sys.executable, "-m", "jobtracker", "--db", str(db_path), *args],
            capture_output=True, env=env, cwd=db_path.parent, timeout=60,
        )

    added = module("add", "-c", "Exemple 株式会社", "-r", "Developer")
    assert added.returncode == 0, added.stderr
    listed = module("list")
    assert listed.returncode == 0, listed.stderr
    assert b"Traceback" not in listed.stderr
    assert b"Exemple ????" in listed.stdout
    assert b"1 application" in listed.stdout
    # Only the display is affected; the stored text is complete.
    assert stored()[0].company == "Exemple 株式会社"
