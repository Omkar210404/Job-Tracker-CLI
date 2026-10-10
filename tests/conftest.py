"""Shared fixtures. Every test gets its own database file in a temporary folder."""

from datetime import date

import pytest

# A fixed "today" so no test depends on the clock.
TODAY = date(2026, 3, 20)


@pytest.fixture
def today():
    return TODAY


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Point the default database at the temp folder, so a test that forgets
    --db still cannot touch the real one in the home directory."""
    monkeypatch.setenv("JOBTRACK_DB", str(tmp_path / "env-default.db"))
    monkeypatch.delenv("JOBTRACK_TODAY", raising=False)
