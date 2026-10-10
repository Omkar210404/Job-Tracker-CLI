"""The transition table, checked pair by pair. No database involved."""

import itertools

import pytest

from jobtracker.errors import ValidationError
from jobtracker.models import Status
from jobtracker.status_rules import ALLOWED_MOVES, check_move, is_allowed

S = Status

# Written out by hand, independently of ALLOWED_MOVES, so a wrong edit to the
# table in the code fails here.
EXPECTED_ALLOWED = {
    (S.SAVED, S.APPLIED),
    (S.SAVED, S.WITHDRAWN),
    (S.APPLIED, S.SCREENING),
    (S.APPLIED, S.INTERVIEW),
    (S.APPLIED, S.OFFER),
    (S.APPLIED, S.REJECTED),
    (S.APPLIED, S.WITHDRAWN),
    (S.SCREENING, S.INTERVIEW),
    (S.SCREENING, S.OFFER),
    (S.SCREENING, S.REJECTED),
    (S.SCREENING, S.WITHDRAWN),
    (S.INTERVIEW, S.OFFER),
    (S.INTERVIEW, S.REJECTED),
    (S.INTERVIEW, S.WITHDRAWN),
    (S.OFFER, S.REJECTED),
    (S.OFFER, S.WITHDRAWN),
}

ALL_PAIRS = list(itertools.product(Status, Status))


def pair_id(pair):
    return f"{pair[0].value}->{pair[1].value}"


def test_table_covers_every_status():
    assert set(ALLOWED_MOVES) == set(Status)


@pytest.mark.parametrize("pair", ALL_PAIRS, ids=pair_id)
def test_every_pair_matches_the_expected_table(pair):
    old, new = pair
    assert is_allowed(old, new) == (pair in EXPECTED_ALLOWED)


@pytest.mark.parametrize("pair", sorted(EXPECTED_ALLOWED), ids=pair_id)
def test_allowed_moves_pass_without_force(pair):
    check_move(*pair)


@pytest.mark.parametrize(
    "pair",
    [pair for pair in ALL_PAIRS if pair[0] is not pair[1] and pair not in EXPECTED_ALLOWED],
    ids=pair_id,
)
def test_other_moves_need_force(pair):
    old, new = pair
    with pytest.raises(ValidationError) as excinfo:
        check_move(old, new)
    message = str(excinfo.value)
    assert f"from '{old.value}' to '{new.value}'" in message
    assert "--force" in message
    check_move(old, new, force=True)


@pytest.mark.parametrize("status", list(Status), ids=lambda s: s.value)
def test_same_status_is_refused_even_with_force(status):
    for force in (False, True):
        with pytest.raises(ValidationError, match=f"already '{status.value}'"):
            check_move(status, status, force=force)


def test_forward_moves_may_skip_stages():
    assert is_allowed(S.APPLIED, S.INTERVIEW)
    assert is_allowed(S.APPLIED, S.OFFER)
    assert is_allowed(S.SCREENING, S.OFFER)


def test_saved_cannot_skip_applying():
    for target in (S.SCREENING, S.INTERVIEW, S.OFFER, S.REJECTED):
        assert not is_allowed(S.SAVED, target)


def test_backward_moves_are_not_allowed():
    order = [S.SAVED, S.APPLIED, S.SCREENING, S.INTERVIEW, S.OFFER]
    for later, earlier in itertools.combinations(reversed(order), 2):
        assert not is_allowed(later, earlier)


@pytest.mark.parametrize("closed", [S.REJECTED, S.WITHDRAWN], ids=lambda s: s.value)
def test_closed_statuses_allow_nothing(closed):
    assert ALLOWED_MOVES[closed] == ()
    with pytest.raises(ValidationError, match="closed status"):
        check_move(closed, S.APPLIED)


def test_refusal_lists_the_allowed_next_statuses():
    with pytest.raises(ValidationError) as excinfo:
        check_move(S.INTERVIEW, S.APPLIED)
    assert "offer, rejected, withdrawn" in str(excinfo.value)
