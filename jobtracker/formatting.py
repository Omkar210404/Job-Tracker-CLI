"""Turn results into plain text for the terminal.

Only ASCII is added around the user's data, so the output looks the same in
every console and can be pasted into a README or an email.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from jobtracker.models import ALL_STATUSES, Application, DueItem, StatusChange, Summary

EMPTY = "-"


def _text(value: object) -> str:
    if value is None or value == "":
        return EMPTY
    if isinstance(value, date):
        return value.isoformat()
    # Keep a cell on one line.
    return " ".join(str(getattr(value, "value", value)).split())


def _shorten(text: str, width: int | None) -> str:
    if width is None or len(text) <= width:
        return text
    return text[: width - 3].rstrip() + "..."


def table(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    max_widths: dict[str, int] | None = None,
    right: Sequence[str] = (),
) -> str:
    """A left-aligned text table. `max_widths` shortens long cells, `right` names
    the columns to align to the right (numbers)."""
    max_widths = max_widths or {}
    cells = [
        [_shorten(_text(value), max_widths.get(header)) for header, value in zip(headers, row)]
        for row in rows
    ]
    widths = [
        max(len(header), *(len(row[index]) for row in cells)) if cells else len(header)
        for index, header in enumerate(headers)
    ]

    def line(values: Sequence[str]) -> str:
        parts = [
            value.rjust(width) if header in right else value.ljust(width)
            for header, value, width in zip(headers, values, widths)
        ]
        return "  ".join(parts).rstrip()

    out = [line(headers), line(["-" * width for width in widths])]
    out.extend(line(row) for row in cells)
    return "\n".join(out)


def count(number: int, noun: str) -> str:
    """'1 application', '3 applications'."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def describe(app: Application) -> str:
    """One short phrase naming an application."""
    return f"{app.role} at {app.company}"


_LIST_WIDTHS = {"Company": 28, "Role": 30, "Location": 18}


def applications_table(apps: Sequence[Application]) -> str:
    headers = ("ID", "Company", "Role", "Status", "Applied", "Follow-up", "Location")
    rows = [
        (app.id, app.company, app.role, app.status, app.date_applied, app.follow_up_date,
         app.location)
        for app in apps
    ]
    return table(headers, rows, max_widths=_LIST_WIDTHS, right=("ID",))


def application_details(app: Application, history: Sequence[StatusChange]) -> str:
    fields = (
        ("Company", app.company),
        ("Role", app.role),
        ("Status", app.status),
        ("Applied", app.date_applied),
        ("First response", app.first_response_date),
        ("Follow-up", app.follow_up_date),
        ("Location", app.location),
        ("Source", app.source),
        ("Link", app.link),
        ("Notes", app.notes),
    )
    width = max(len(label) for label, _ in fields) + 1
    lines = [f"Application {app.id}"]
    lines.extend(f"  {(label + ':').ljust(width)}  {_text(value)}" for label, value in fields)
    if history:
        lines.append("")
        lines.append("History")
        for change in history:
            if change.old_status is None:
                what = f"added as {change.new_status.value}"
            else:
                what = f"{change.old_status.value} -> {change.new_status.value}"
            note = f"  ({_text(change.note)})" if change.note else ""
            lines.append(f"  {change.changed_on.isoformat()}  {what}{note}")
    return "\n".join(lines)


def due_phrase(days_overdue: int) -> str:
    if days_overdue > 0:
        return f"overdue by {count(days_overdue, 'day')}"
    if days_overdue == 0:
        return "due today"
    return f"in {count(-days_overdue, 'day')}"


def due_table(items: Sequence[DueItem]) -> str:
    headers = ("ID", "Company", "Role", "Status", "Follow-up", "Due")
    rows = [
        (
            item.application.id,
            item.application.company,
            item.application.role,
            item.application.status,
            item.application.follow_up_date,
            due_phrase(item.days_overdue),
        )
        for item in items
    ]
    return table(headers, rows, max_widths=_LIST_WIDTHS, right=("ID",))


def due_totals(items: Sequence[DueItem]) -> str:
    """'2 overdue, 1 due today, 1 upcoming', leaving out the groups that are empty."""
    overdue = sum(1 for item in items if item.days_overdue > 0)
    today = sum(1 for item in items if item.days_overdue == 0)
    upcoming = sum(1 for item in items if item.days_overdue < 0)
    parts = []
    if overdue:
        parts.append(f"{overdue} overdue")
    if today:
        parts.append(f"{today} due today")
    if upcoming:
        parts.append(f"{upcoming} upcoming")
    return ", ".join(parts)


def _range_phrase(date_from: date | None, date_to: date | None) -> str | None:
    if date_from and date_to:
        return f"applied from {date_from} to {date_to}"
    if date_from:
        return f"applied on or after {date_from}"
    if date_to:
        return f"applied on or before {date_to}"
    return None


def summary_text(summary: Summary) -> str:
    lines = [f"Applications: {summary.total}"]
    scope = _range_phrase(summary.date_from, summary.date_to)
    if scope:
        lines.append(f"Only applications {scope} are counted.")
    lines.append("")
    lines.append(
        table(
            ("Status", "Count"),
            [(status, summary.by_status[status]) for status in ALL_STATUSES],
            right=("Count",),
        )
    )
    lines.append("")

    if summary.response_rate is None:
        lines.append("Response rate: n/a (no application has an applied date yet)")
    else:
        lines.append(
            f"Response rate: {summary.response_rate * 100:.1f}% "
            f"({summary.responded} of {summary.applied} applied got a response)"
        )
    label = "Average days from applied to first response:"
    if summary.avg_days_to_first_response is None:
        lines.append(f"{label} n/a (no response with a known date yet)")
    else:
        lines.append(
            f"{label} {summary.avg_days_to_first_response:.1f} "
            f"(based on {count(summary.avg_based_on, 'application')})"
        )
    return "\n".join(lines)
