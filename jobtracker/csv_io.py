"""CSV export and import, built on the service layer.

The file holds one application per row. Status history is not part of it.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from jobtracker.errors import CsvImportError, ValidationError
from jobtracker.models import Application, ImportResult, NewApplication, Status
from jobtracker.service import TrackerService
from jobtracker.validation import parse_optional_date, parse_status

COLUMNS = (
    "id",
    "company",
    "role",
    "link",
    "location",
    "source",
    "status",
    "date_applied",
    "follow_up_date",
    "first_response_date",
    "notes",
)
REQUIRED_COLUMNS = ("company", "role")
DATE_COLUMNS = ("date_applied", "follow_up_date", "first_response_date")


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Status):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _row(app: Application) -> list[str]:
    return [_cell(getattr(app, column)) for column in COLUMNS]


def export_csv(service: TrackerService, path: str | Path, *, overwrite: bool = False) -> int:
    """Write every application to `path` as UTF-8 CSV. Returns the number of rows written."""
    path = Path(path)
    if path.is_dir():
        raise ValidationError(f"'{path}' is a folder. Give a file name, for example jobs.csv.")
    if path.exists() and not overwrite:
        raise ValidationError(f"'{path}' already exists. Use --overwrite to replace it.")
    apps = service.find()
    try:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLUMNS)
            writer.writerows(_row(app) for app in apps)
    except OSError as exc:
        raise ValidationError(f"Cannot write '{path}': {exc.strerror or exc}.") from None
    return len(apps)


def _normalise_header(name: str | None) -> str:
    return (name or "").strip().lower().replace(" ", "_").replace("-", "_")


def _check_header(header: list[str]) -> list[str]:
    problems = []
    seen = set()
    for name in header:
        if not name:
            problems.append("line 1: a column has no name")
        elif name in seen:
            problems.append(f"line 1: the column '{name}' appears more than once")
        elif name not in COLUMNS:
            problems.append(
                f"line 1: unknown column '{name}' (known columns: {', '.join(COLUMNS)})"
            )
        seen.add(name)
    for name in REQUIRED_COLUMNS:
        if name not in seen:
            problems.append(f"line 1: the required column '{name}' is missing")
    return problems


def read_csv(service: TrackerService, path: str | Path, *, today: date) -> list[NewApplication]:
    """Read and validate a CSV file without storing anything.

    Raises CsvImportError listing every problem with its line number, so the
    file can be fixed in one pass.
    """
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"File not found: '{path}'.")
    try:
        # utf-8-sig drops the invisible marker Excel puts at the start of a file.
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return _read(service, handle, today)
    except UnicodeDecodeError:
        raise ValidationError(
            f"'{path}' is not UTF-8 text. Save it as 'CSV UTF-8' and try again."
        ) from None
    except csv.Error as exc:
        raise ValidationError(f"'{path}' could not be read as CSV: {exc}.") from None
    except OSError as exc:
        raise ValidationError(f"Cannot read '{path}': {exc.strerror or exc}.") from None


def _read(service: TrackerService, handle, today: date) -> list[NewApplication]:
    reader = csv.reader(handle)
    header = next(reader, None)
    if header is None or not any(cell.strip() for cell in header):
        raise CsvImportError(["the file is empty; the first line must name the columns"])
    header = [_normalise_header(name) for name in header]
    problems = _check_header(header)
    if problems:
        raise CsvImportError(problems)

    rows: list[NewApplication] = []
    for cells in reader:
        line = reader.line_num
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) > len(header):
            problems.append(
                f"line {line}: {len(cells)} values but only {len(header)} columns"
            )
            continue
        # A short row is read as if its missing cells were blank.
        values = dict(zip(header, cells))
        row_problems: list[str] = []

        def text(column: str) -> str:
            return values.get(column, "").strip()

        status = Status.APPLIED
        if text("status"):
            try:
                status = parse_status(text("status"))
            except ValidationError as exc:
                row_problems.append(str(exc))
        dates: dict[str, date | None] = {}
        for column in DATE_COLUMNS:
            try:
                dates[column] = parse_optional_date(text(column), field=column, today=today)
            except ValidationError as exc:
                row_problems.append(str(exc))

        if not row_problems:
            try:
                rows.append(
                    service.check_new(
                        NewApplication(
                            company=text("company"),
                            role=text("role"),
                            link=text("link"),
                            location=text("location"),
                            source=text("source"),
                            notes=text("notes"),
                            status=status,
                            **dates,
                        ),
                        today=today,
                    )
                )
            except ValidationError as exc:
                row_problems.append(str(exc))
        problems.extend(f"line {line}: {problem}" for problem in row_problems)

    if problems:
        raise CsvImportError(problems)
    return rows


def import_csv(
    service: TrackerService, path: str | Path, *, today: date, dry_run: bool = False
) -> ImportResult:
    """Validate the whole file, then import it. Any invalid row means nothing is imported."""
    rows = read_csv(service, path, today=today)
    return service.import_applications(rows, today=today, dry_run=dry_run)
