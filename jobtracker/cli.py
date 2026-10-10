"""CLI layer: parse arguments, call the service, print the result.

No rules live here. Expected errors become one "Error: ..." line on stderr and
exit code 1; a wrong command line is reported by Typer with exit code 2.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer

from jobtracker import __version__, csv_io, db
from jobtracker import formatting as fmt
from jobtracker.errors import JobTrackerError, ValidationError
from jobtracker.models import ALL_STATUSES, CLOSED_STATUSES, Status
from jobtracker.service import UNSET, TrackerService
from jobtracker.validation import parse_date, parse_optional_date, parse_status

STATUS_NAMES = ", ".join(status.value for status in ALL_STATUSES)
DATE_HELP = "YYYY-MM-DD or 'today'"

app = typer.Typer(
    name="jobtrack",
    help=(
        "Track job applications in a local SQLite file.\n\n"
        f"Statuses: {STATUS_NAMES}.\n\n"
        "Dates are written as YYYY-MM-DD, or the word 'today'."
    ),
    add_completion=False,
    no_args_is_help=True,
    # Plain help and plain errors: the same text in every terminal width.
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def default_db_path() -> Path:
    return Path.home() / ".jobtrack" / "jobtrack.db"


def current_date() -> date:
    """Today's date. JOBTRACK_TODAY overrides the clock, for the demo and for tests."""
    override = os.environ.get("JOBTRACK_TODAY", "").strip()
    if not override:
        return date.today()
    try:
        return date.fromisoformat(override)
    except ValueError:
        raise ValidationError(
            f"JOBTRACK_TODAY is set to '{override}', which is not a date like 2026-03-20."
        ) from None


def fail(message: object) -> typer.Exit:
    typer.echo(f"Error: {message}", err=True)
    return typer.Exit(code=1)


@contextmanager
def session(ctx: typer.Context) -> Iterator[tuple[TrackerService, date]]:
    """Open the database for one command and turn expected errors into messages."""
    db_path: Path = ctx.obj
    conn = None
    try:
        today = current_date()
        conn = db.connect(db_path)
        yield TrackerService(conn), today
    except JobTrackerError as exc:
        raise fail(exc) from None
    except (sqlite3.Error, OSError) as exc:
        raise fail(f"cannot use the database at '{db_path}': {exc}") from None
    finally:
        if conn is not None:
            conn.close()


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"jobtrack {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    db_path: Annotated[
        Optional[Path],
        typer.Option(
            "--db",
            metavar="FILE",
            envvar="JOBTRACK_DB",
            help="Database file to use. Default: .jobtrack/jobtrack.db in your home folder.",
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_show_version, is_eager=True, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    ctx.obj = db_path or default_db_path()
    # A company name the console cannot show should print as '?', not crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _statuses(values: Optional[list[str]]) -> list[Status]:
    """Parse --status values; each may be one name or several separated by commas."""
    names = [part for value in values or [] for part in value.split(",") if part.strip()]
    return [parse_status(name) for name in names]


@app.command()
def add(
    ctx: typer.Context,
    company: Annotated[str, typer.Option("--company", "-c", help="Company name.")],
    role: Annotated[str, typer.Option("--role", "-r", help="Job title.")],
    link: Annotated[Optional[str], typer.Option(help="Link to the job posting.")] = None,
    location: Annotated[Optional[str], typer.Option(help="City, country or 'Remote'.")] = None,
    source: Annotated[
        Optional[str], typer.Option(help="Where you found it, for example 'referral'.")
    ] = None,
    notes: Annotated[Optional[str], typer.Option(help="Free text.")] = None,
    date_applied: Annotated[
        Optional[str],
        typer.Option("--date-applied", "-d", metavar="DATE", help=f"{DATE_HELP}. Default: today."),
    ] = None,
    status: Annotated[
        str,
        typer.Option(help="'applied', or 'saved' for a job you have not applied to yet."),
    ] = "applied",
) -> None:
    """Add an application."""
    with session(ctx) as (svc, today):
        created = svc.add(
            company=company,
            role=role,
            link=link,
            location=location,
            source=source,
            notes=notes,
            status=parse_status(status),
            date_applied=parse_optional_date(date_applied, field="applied date", today=today),
            today=today,
        )
        if created.date_applied:
            state = f"applied on {created.date_applied}"
        else:
            state = "saved, not applied yet"
        typer.echo(f"Added application {created.id}: {fmt.describe(created)} ({state}).")


@app.command("list")
def list_(
    ctx: typer.Context,
    status: Annotated[
        Optional[list[str]],
        typer.Option(
            "--status", "-s", help="Only this status. Repeat the option or separate with commas."
        ),
    ] = None,
    company: Annotated[
        Optional[str], typer.Option("--company", "-c", help="Company name contains this text.")
    ] = None,
    date_from: Annotated[
        Optional[str], typer.Option("--from", metavar="DATE", help=f"Applied on or after this date ({DATE_HELP}).")
    ] = None,
    date_to: Annotated[
        Optional[str], typer.Option("--to", metavar="DATE", help=f"Applied on or before this date ({DATE_HELP}).")
    ] = None,
    search: Annotated[
        Optional[str],
        typer.Option(help="Text to look for in company, role, location, source and notes."),
    ] = None,
    sort: Annotated[
        str, typer.Option(help=f"Sort by: {', '.join(db.SORT_KEYS)}.")
    ] = "id",
    desc: Annotated[bool, typer.Option("--desc", help="Reverse the sort order.")] = False,
    limit: Annotated[Optional[int], typer.Option(metavar="N", help="Show at most this many.")] = None,
) -> None:
    """List applications. Filters combine: every one given must match."""
    with session(ctx) as (svc, today):
        filtered = any(value for value in (status, company, date_from, date_to, search))
        apps = svc.find(
            statuses=_statuses(status),
            company=company,
            date_from=parse_optional_date(date_from, field="--from date", today=today),
            date_to=parse_optional_date(date_to, field="--to date", today=today),
            search=search,
            sort=sort.strip().lower(),
            descending=desc,
            limit=limit,
        )
        if not apps:
            if filtered:
                typer.echo("No applications match.")
            else:
                typer.echo(
                    "No applications yet. Add one with: "
                    'jobtrack add --company "..." --role "..."'
                )
            return
        typer.echo(fmt.applications_table(apps))
        typer.echo(f"\n{fmt.count(len(apps), 'application')}")


@app.command()
def show(
    ctx: typer.Context,
    app_id: Annotated[int, typer.Argument(metavar="ID", help="Application id, as shown by 'list'.")],
) -> None:
    """Show every field of one application and its status history."""
    with session(ctx) as (svc, _today):
        typer.echo(fmt.application_details(svc.get(app_id), svc.history(app_id)))


@app.command()
def status(
    ctx: typer.Context,
    app_id: Annotated[int, typer.Argument(metavar="ID", help="Application id.")],
    new_status: Annotated[
        str, typer.Argument(metavar="STATUS", help=f"One of: {STATUS_NAMES}.")
    ],
    on: Annotated[
        Optional[str],
        typer.Option("--date", "-d", metavar="DATE", help=f"Day the change happened ({DATE_HELP}). Default: today."),
    ] = None,
    note: Annotated[Optional[str], typer.Option(help="Short note kept in the history.")] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Allow a move the rules refuse, such as going back a stage, to fix a mistake.",
        ),
    ] = False,
) -> None:
    """Move an application to a new status.

    Normal moves go forward: saved -> applied -> screening -> interview -> offer.
    Stages can be skipped. 'rejected' and 'withdrawn' close the application.
    """
    with session(ctx) as (svc, today):
        before = svc.get(app_id)
        changed_on = parse_optional_date(on, field="status change date", today=today) or today
        after = svc.change_status(
            app_id, parse_status(new_status), on=changed_on, today=today, note=note, force=force
        )
        typer.echo(
            f"Application {app_id}: {before.status.value} -> {after.status.value} on {changed_on}."
        )
        if before.first_response_date is None and after.first_response_date is not None:
            waited = (after.first_response_date - after.date_applied).days
            typer.echo(f"First response recorded, {fmt.count(waited, 'day')} after applying.")


@app.command()
def followup(
    ctx: typer.Context,
    app_id: Annotated[int, typer.Argument(metavar="ID", help="Application id.")],
    when: Annotated[
        Optional[str],
        typer.Argument(
            metavar="[DATE]", help="YYYY-MM-DD, 'today', or +7d for seven days from today."
        ),
    ] = None,
    clear: Annotated[bool, typer.Option("--clear", help="Remove the follow-up date.")] = False,
) -> None:
    """Set or remove the follow-up date of an application."""
    with session(ctx) as (svc, today):
        if clear and when:
            raise ValidationError("Give a date or --clear, not both.")
        if not clear and not when:
            raise ValidationError("Give a follow-up date, or --clear to remove it.")
        if clear:
            svc.set_follow_up(app_id, None)
            typer.echo(f"Application {app_id}: follow-up date removed.")
            return
        target = parse_date(when, field="follow-up date", today=today, allow_relative=True)
        updated = svc.set_follow_up(app_id, target)
        typer.echo(f"Application {app_id}: follow-up set for {target}.")
        if updated.status in CLOSED_STATUSES:
            typer.echo(
                f"Note: this application is {updated.status.value}, "
                "so it will not appear in 'jobtrack due'."
            )


@app.command()
def due(
    ctx: typer.Context,
    within: Annotated[
        int, typer.Option(metavar="N", help="Also show follow-ups coming up in the next N days.")
    ] = 0,
) -> None:
    """Show applications whose follow-up date is today or has passed.

    Rejected and withdrawn applications are left out.
    """
    with session(ctx) as (svc, today):
        items = svc.due(today=today, within=within)
        if not items:
            ahead = f" or in the next {fmt.count(within, 'day')}" if within else ""
            typer.echo(f"No follow-ups due as of {today}{ahead}.")
            return
        typer.echo(f"Follow-ups as of {today}:\n")
        typer.echo(fmt.due_table(items))
        typer.echo(f"\n{fmt.due_totals(items)}")


@app.command()
def edit(
    ctx: typer.Context,
    app_id: Annotated[int, typer.Argument(metavar="ID", help="Application id.")],
    company: Annotated[Optional[str], typer.Option("--company", "-c", help="New company name.")] = None,
    role: Annotated[Optional[str], typer.Option("--role", "-r", help="New job title.")] = None,
    link: Annotated[Optional[str], typer.Option(help="New link.")] = None,
    location: Annotated[Optional[str], typer.Option(help="New location.")] = None,
    source: Annotated[Optional[str], typer.Option(help="New source.")] = None,
    notes: Annotated[Optional[str], typer.Option(help="New notes (replaces the old ones).")] = None,
    date_applied: Annotated[
        Optional[str], typer.Option("--date-applied", "-d", metavar="DATE", help=f"New applied date ({DATE_HELP}).")
    ] = None,
) -> None:
    """Change fields of an application. Only the options you give are changed.

    Pass an empty value to clear an optional field, for example --notes "".
    The status is changed with the 'status' command, the follow-up date with 'followup'.
    """
    with session(ctx) as (svc, today):
        changes: dict[str, object] = {
            name: value
            for name, value in (
                ("company", company),
                ("role", role),
                ("link", link),
                ("location", location),
                ("source", source),
                ("notes", notes),
            )
            if value is not None
        }
        if date_applied is not None:
            changes["date_applied"] = parse_date(date_applied, field="applied date", today=today)
        updated = svc.edit(app_id, today=today, **changes)
        typer.echo(f"Updated application {app_id}: {', '.join(changes)}.\n")
        typer.echo(fmt.application_details(updated, []))


@app.command()
def delete(
    ctx: typer.Context,
    app_id: Annotated[int, typer.Argument(metavar="ID", help="Application id.")],
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Delete without asking for confirmation.")
    ] = False,
) -> None:
    """Delete an application and its history. Asks for confirmation first."""
    with session(ctx) as (svc, _today):
        target = svc.get(app_id)
        if not yes:
            typer.echo(fmt.application_details(target, []))
            typer.echo("")
            if not typer.confirm("Delete this application? This cannot be undone.", default=False):
                typer.echo("Nothing deleted.")
                return
        svc.delete(app_id)
        typer.echo(f"Deleted application {app_id}: {fmt.describe(target)}.")


@app.command(short_help="Show counts per status, response rate and days to first response.")
def summary(
    ctx: typer.Context,
    date_from: Annotated[
        Optional[str],
        typer.Option("--from", metavar="DATE", help=f"Count only applications applied on or after ({DATE_HELP})."),
    ] = None,
    date_to: Annotated[
        Optional[str],
        typer.Option("--to", metavar="DATE", help=f"Count only applications applied on or before ({DATE_HELP})."),
    ] = None,
) -> None:
    """Show counts per status, response rate and average days to first response.

    Every number is computed from your own applications.
    A response is reaching screening, interview, offer or rejected; withdrawing is not.
    """
    with session(ctx) as (svc, today):
        result = svc.summary(
            date_from=parse_optional_date(date_from, field="--from date", today=today),
            date_to=parse_optional_date(date_to, field="--to date", today=today),
        )
        typer.echo(fmt.summary_text(result))


@app.command()
def export(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(metavar="FILE", help="CSV file to write.")],
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace the file if it already exists.")
    ] = False,
) -> None:
    """Export all applications to a CSV file."""
    with session(ctx) as (svc, _today):
        written = csv_io.export_csv(svc, path, overwrite=overwrite)
        typer.echo(f"Exported {fmt.count(written, 'application')} to {path}.")


@app.command("import")
def import_(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(metavar="FILE", help="CSV file to read.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Check the file and report, but store nothing.")
    ] = False,
) -> None:
    """Import applications from a CSV file.

    The file needs the columns company and role. Optional columns: link, location,
    source, status, date_applied, follow_up_date, first_response_date, notes.
    If any row is invalid nothing is imported. Rows already stored (same company,
    role and applied date) are skipped.
    """
    with session(ctx) as (svc, today):
        result = csv_io.import_csv(svc, path, today=today, dry_run=dry_run)
        duplicates = fmt.count(result.skipped_duplicates, "duplicate")
        if result.dry_run:
            typer.echo(
                f"Dry run: {fmt.count(result.imported, 'application')} would be imported, "
                f"{duplicates} skipped. Nothing was stored."
            )
        else:
            typer.echo(
                f"Imported {fmt.count(result.imported, 'application')} from {path}, "
                f"skipped {duplicates}."
            )


def run() -> None:
    """Entry point for `python -m jobtracker`."""
    app(prog_name="jobtrack")
