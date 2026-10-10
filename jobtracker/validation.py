"""Turn text typed by the user (or read from a CSV) into checked values.

Every function raises ValidationError with a message meant to be shown as is.
"""

from __future__ import annotations

import difflib
import re
from datetime import date, timedelta

from jobtracker.errors import ValidationError
from jobtracker.models import ALL_STATUSES, Status

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RELATIVE_DAYS = re.compile(r"^\+(\d{1,4})d$")


def parse_date(
    text: str, *, field: str, today: date, allow_relative: bool = False
) -> date:
    """Parse `YYYY-MM-DD` or `today`; with `allow_relative` also `+Nd` (N days from today).

    `field` names the value in the error message, for example "applied date".
    """
    value = (text or "").strip()
    lowered = value.lower()
    if not value:
        raise ValidationError(f"The {field} is empty. Use YYYY-MM-DD.")
    if lowered == "today":
        return today
    if allow_relative:
        match = _RELATIVE_DAYS.match(lowered)
        if match:
            return today + timedelta(days=int(match.group(1)))
    if not _ISO_DATE.match(value):
        example = today.isoformat()
        extra = ", 'today' or +7d" if allow_relative else " or 'today'"
        raise ValidationError(
            f"Invalid {field} '{value}'. Use YYYY-MM-DD (for example {example}){extra}."
        )
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError(
            f"Invalid {field} '{value}': that day does not exist in the calendar."
        ) from None


def parse_optional_date(
    text: str | None, *, field: str, today: date, allow_relative: bool = False
) -> date | None:
    """Like parse_date, but blank or missing text means "no date"."""
    if text is None or not text.strip():
        return None
    return parse_date(text, field=field, today=today, allow_relative=allow_relative)


def parse_status(text: str) -> Status:
    """Parse a status name, ignoring case and surrounding spaces."""
    value = (text or "").strip().lower()
    for status in ALL_STATUSES:
        if status.value == value:
            return status
    names = [status.value for status in ALL_STATUSES]
    hint = ""
    close = difflib.get_close_matches(value, names, n=1)
    if close:
        hint = f" Did you mean '{close[0]}'?"
    shown = (text or "").strip()
    raise ValidationError(
        f"Unknown status '{shown}'.{hint} Valid statuses: {', '.join(names)}."
    )


def require_text(text: str | None, *, field: str) -> str:
    """Return the trimmed text, refusing blank values. `field` is used in the message."""
    value = (text or "").strip()
    if not value:
        raise ValidationError(f"The {field} cannot be empty.")
    return value


def clean_optional(text: str | None) -> str | None:
    """Trim optional text. Blank becomes None so "no value" is stored one way only."""
    if text is None:
        return None
    value = text.strip()
    return value or None
