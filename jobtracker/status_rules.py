"""Which status moves are allowed. Pure functions, no database.

Pipeline: saved -> applied -> screening -> interview -> offer.
Closed:   rejected, withdrawn.

Forward moves may skip stages, because real hiring processes do. Anything
outside the table (going backwards, leaving a closed status) needs `force`,
which exists to correct a wrong entry.
"""

from __future__ import annotations

from jobtracker.errors import ValidationError
from jobtracker.models import CLOSED_STATUSES, Status

S = Status

ALLOWED_MOVES: dict[Status, tuple[Status, ...]] = {
    S.SAVED: (S.APPLIED, S.WITHDRAWN),
    S.APPLIED: (S.SCREENING, S.INTERVIEW, S.OFFER, S.REJECTED, S.WITHDRAWN),
    S.SCREENING: (S.INTERVIEW, S.OFFER, S.REJECTED, S.WITHDRAWN),
    S.INTERVIEW: (S.OFFER, S.REJECTED, S.WITHDRAWN),
    S.OFFER: (S.REJECTED, S.WITHDRAWN),
    S.REJECTED: (),
    S.WITHDRAWN: (),
}


def is_allowed(old: Status, new: Status) -> bool:
    """True when `old` -> `new` is a normal move that needs no force."""
    return new in ALLOWED_MOVES[old]


def check_move(old: Status, new: Status, *, force: bool = False) -> None:
    """Raise ValidationError unless the move is allowed. `force` permits any real change."""
    if old is new:
        raise ValidationError(f"The status is already '{new.value}'.")
    if force or is_allowed(old, new):
        return
    if old in CLOSED_STATUSES:
        reason = f"'{old.value}' is a closed status, so it normally does not change"
    else:
        allowed = ", ".join(status.value for status in ALLOWED_MOVES[old])
        reason = f"from '{old.value}' the next status can be: {allowed}"
    raise ValidationError(
        f"Cannot move from '{old.value}' to '{new.value}': {reason}. "
        "Use --force if you are correcting a mistake."
    )
