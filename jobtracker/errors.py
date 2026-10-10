"""Errors the user can cause. The CLI prints their message instead of a traceback."""


class JobTrackerError(Exception):
    """Base class for every expected error."""


class ValidationError(JobTrackerError):
    """Input that is not acceptable: a bad date, an unknown status, a move the rules refuse."""


class NotFoundError(JobTrackerError):
    """No application has the given id."""


class CsvImportError(ValidationError):
    """A CSV file that cannot be imported. `problems` holds one message per issue."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        count = len(self.problems)
        noun = "problem" if count == 1 else "problems"
        lines = [f"CSV not imported, {count} {noun} found:"]
        lines.extend(f"  {problem}" for problem in self.problems)
        super().__init__("\n".join(lines))
