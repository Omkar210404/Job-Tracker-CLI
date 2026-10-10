"""Database layer: the only module that contains SQL.

It stores and fetches rows and knows nothing about the status rules or what a
valid application is. Functions never commit; the caller decides where a
transaction ends (`with conn:`), so several writes can succeed or fail together.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from jobtracker.models import Application, NewApplication, Status, StatusChange

SCHEMA_VERSION = 1

_STATUS_LIST = ", ".join(f"'{status.value}'" for status in Status)

_SCHEMA = f"""
CREATE TABLE applications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company             TEXT NOT NULL CHECK (trim(company) <> ''),
    role                TEXT NOT NULL CHECK (trim(role) <> ''),
    link                TEXT,
    location            TEXT,
    source              TEXT,
    notes               TEXT,
    status              TEXT NOT NULL CHECK (status IN ({_STATUS_LIST})),
    date_applied        TEXT,
    follow_up_date      TEXT,
    first_response_date TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE status_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications (id) ON DELETE CASCADE,
    old_status     TEXT,
    new_status     TEXT NOT NULL CHECK (new_status IN ({_STATUS_LIST})),
    changed_on     TEXT NOT NULL,
    note           TEXT
);

CREATE INDEX idx_history_application ON status_history (application_id);
"""

# Columns a caller may change through update_application.
UPDATABLE_COLUMNS = frozenset(
    {
        "company",
        "role",
        "link",
        "location",
        "source",
        "notes",
        "status",
        "date_applied",
        "follow_up_date",
        "first_response_date",
    }
)

_STATUS_ORDER = (
    "CASE status "
    + " ".join(f"WHEN '{status.value}' THEN {position}" for position, status in enumerate(Status))
    + " END"
)

# Sort keys offered by query_applications, mapped to fixed (ascending, descending)
# SQL so no user text ever reaches ORDER BY. Empty dates sort last either way,
# and status sorts in pipeline order.
SORT_KEYS = {
    "id": ("id", "id DESC"),
    "company": ("company COLLATE NOCASE, id", "company COLLATE NOCASE DESC, id"),
    "applied": (
        "date_applied IS NULL, date_applied, id",
        "date_applied IS NULL, date_applied DESC, id",
    ),
    "followup": (
        "follow_up_date IS NULL, follow_up_date, id",
        "follow_up_date IS NULL, follow_up_date DESC, id",
    ),
    "status": (f"{_STATUS_ORDER}, id", f"{_STATUS_ORDER} DESC, id"),
}


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database at `path`, creating the file, its folder and the tables if needed."""
    path = Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # SQLite leaves foreign keys off unless asked, per connection.
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the tables on a new database. An existing one is left as it is."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version != 0:
        raise sqlite3.DatabaseError(
            f"database has schema version {version}, this program understands {SCHEMA_VERSION}"
        )
    has_tables = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'applications'"
    ).fetchone()
    if has_tables:
        raise sqlite3.DatabaseError("database has an applications table but no schema version")
    conn.executescript(f"BEGIN; {_SCHEMA} PRAGMA user_version = {SCHEMA_VERSION}; COMMIT;")


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_date(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None


def _to_application(row: sqlite3.Row) -> Application:
    return Application(
        id=row["id"],
        company=row["company"],
        role=row["role"],
        link=row["link"],
        location=row["location"],
        source=row["source"],
        notes=row["notes"],
        status=Status(row["status"]),
        date_applied=_to_date(row["date_applied"]),
        follow_up_date=_to_date(row["follow_up_date"]),
        first_response_date=_to_date(row["first_response_date"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_status_change(row: sqlite3.Row) -> StatusChange:
    return StatusChange(
        id=row["id"],
        application_id=row["application_id"],
        old_status=Status(row["old_status"]) if row["old_status"] else None,
        new_status=Status(row["new_status"]),
        changed_on=date.fromisoformat(row["changed_on"]),
        note=row["note"],
    )


def insert_application(conn: sqlite3.Connection, new: NewApplication, *, now: str) -> int:
    """Insert one application and return its id. `now` is the timestamp to record."""
    cursor = conn.execute(
        """
        INSERT INTO applications (
            company, role, link, location, source, notes, status,
            date_applied, follow_up_date, first_response_date, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new.company,
            new.role,
            new.link,
            new.location,
            new.source,
            new.notes,
            new.status.value,
            _iso(new.date_applied),
            _iso(new.follow_up_date),
            _iso(new.first_response_date),
            now,
            now,
        ),
    )
    return cursor.lastrowid


def get_application(conn: sqlite3.Connection, app_id: int) -> Application | None:
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    return _to_application(row) if row else None


def update_application(
    conn: sqlite3.Connection, app_id: int, changes: dict[str, object], *, now: str
) -> bool:
    """Set the given columns on one application. Returns False when the id does not exist."""
    unknown = set(changes) - UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"cannot update column(s): {', '.join(sorted(unknown))}")
    values: dict[str, object] = {}
    for column, value in changes.items():
        if isinstance(value, Status):
            value = value.value
        elif isinstance(value, date):
            value = value.isoformat()
        values[column] = value
    assignments = ", ".join(f"{column} = ?" for column in values)
    separator = ", " if assignments else ""
    cursor = conn.execute(
        f"UPDATE applications SET {assignments}{separator}updated_at = ? WHERE id = ?",
        (*values.values(), now, app_id),
    )
    return cursor.rowcount == 1


def delete_application(conn: sqlite3.Connection, app_id: int) -> bool:
    """Delete one application; its history rows go with it. Returns False for an unknown id."""
    cursor = conn.execute("DELETE FROM applications WHERE id = ?", (app_id,))
    return cursor.rowcount == 1


def _like(text: str) -> str:
    """A LIKE pattern matching `text` anywhere, with % and _ taken literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def query_applications(
    conn: sqlite3.Connection,
    *,
    statuses: list[Status] | None = None,
    company: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    follow_up_until: date | None = None,
    sort: str = "id",
    descending: bool = False,
    limit: int | None = None,
) -> list[Application]:
    """Return applications matching every given filter.

    `date_from` / `date_to` are inclusive bounds on the applied date, so they
    leave out applications that have none. `search` looks in company, role,
    location, source and notes. Text matching ignores case for ASCII letters.
    """
    if sort not in SORT_KEYS:
        raise ValueError(f"unknown sort key: {sort}")
    where: list[str] = []
    params: list[object] = []
    if statuses:
        where.append(f"status IN ({', '.join('?' for _ in statuses)})")
        params.extend(status.value for status in statuses)
    if company:
        where.append("company LIKE ? ESCAPE '\\'")
        params.append(_like(company))
    if date_from is not None:
        where.append("date_applied >= ?")
        params.append(date_from.isoformat())
    if date_to is not None:
        where.append("date_applied <= ?")
        params.append(date_to.isoformat())
    if search:
        columns = ("company", "role", "location", "source", "notes")
        where.append("(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in columns) + ")")
        params.extend([_like(search)] * len(columns))
    if follow_up_until is not None:
        where.append("follow_up_date <= ?")
        params.append(follow_up_until.isoformat())

    sql = "SELECT * FROM applications"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {SORT_KEYS[sort][1 if descending else 0]}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [_to_application(row) for row in conn.execute(sql, params)]


def find_duplicate(
    conn: sqlite3.Connection, company: str, role: str, date_applied: date | None
) -> int | None:
    """Id of an application with the same company, role (ignoring case) and applied date."""
    row = conn.execute(
        """
        SELECT id FROM applications
        WHERE company = ? COLLATE NOCASE AND role = ? COLLATE NOCASE AND date_applied IS ?
        ORDER BY id LIMIT 1
        """,
        (company, role, _iso(date_applied)),
    ).fetchone()
    return row["id"] if row else None


def insert_status_change(
    conn: sqlite3.Connection,
    app_id: int,
    *,
    old_status: Status | None,
    new_status: Status,
    changed_on: date,
    note: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO status_history (application_id, old_status, new_status, changed_on, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            app_id,
            old_status.value if old_status else None,
            new_status.value,
            changed_on.isoformat(),
            note,
        ),
    )
    return cursor.lastrowid


def get_history(conn: sqlite3.Connection, app_id: int) -> list[StatusChange]:
    """Status changes of one application in the order they were recorded."""
    rows = conn.execute(
        "SELECT * FROM status_history WHERE application_id = ? ORDER BY id", (app_id,)
    )
    return [_to_status_change(row) for row in rows]
