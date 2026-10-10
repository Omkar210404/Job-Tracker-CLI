"""Run job-tracker-cli end to end on SYNTHETIC SAMPLE DATA and print what happens.

Every company, role and link in this file is invented. Nothing here describes
a real employer or a real application.

The script runs the real command-line program (`python -m jobtracker ...`) as a
separate process for every step, against a database in a temporary folder that
is deleted at the end. Your own database is never opened. "Today" is fixed with
JOBTRACK_TODAY so the due / overdue output is the same on every run.

    python scripts/demo.py            print the transcript
    python scripts/demo.py --write    also save it to examples/demo_output.txt

The README examples are copied from that file, and tests/test_demo.py fails if
they stop matching a fresh run.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = REPO_ROOT / "examples" / "demo_output.txt"
DEMO_TODAY = "2026-03-20"
NOTE = "synthetic sample"

# (company, role, location, source, applied date or None for "saved")
APPLICATIONS = [
    ("Northwind Example Ltd", "Junior Python Developer", "Pune", "job board", "2026-02-10"),
    ("Bluefield Sample Systems", "ML Engineer Intern", "Remote", "referral", "2026-02-12"),
    ("Cedar Demo Analytics", "Data Analyst", "Mumbai", "company site", "2026-02-18"),
    ("Harbor Placeholder Tech", "Backend Developer (Python)", "Bengaluru", "job board",
     "2026-02-20"),
    ("Lumen Fictional Labs", "AI Engineer (Fresher)", "Hyderabad", "campus", "2026-02-25"),
    ("Quartz Mock Software", "Python Developer", "Remote", "job board", "2026-03-02"),
    ("Pinecrest Sample Data", "Junior Data Scientist", "Pune", "referral", "2026-03-05"),
    ("Orchid Example Works", "NLP Engineer Intern", "Mumbai", "company site", "2026-03-09"),
    ("Tidewater Demo Co", "Software Engineer Trainee", "Chennai", "job board", "2026-03-12"),
    ("Maple Placeholder AI", "ML Engineer (Entry Level)", "Remote", "campus", "2026-03-16"),
    ("Granite Fictional Systems", "Data Engineer Trainee", "Pune", "job board", None),
    ("Willow Mock Robotics", "Computer Vision Intern", "Bengaluru", "referral", None),
]

# (application id, new status, date, note)
STATUS_CHANGES = [
    (1, "screening", "2026-02-17", "phone screen booked"),
    (1, "interview", "2026-02-26", None),
    (1, "offer", "2026-03-12", None),
    (2, "rejected", "2026-02-24", None),
    (3, "screening", "2026-03-03", None),
    (5, "interview", "2026-03-09", "skipped the phone screen"),
    (6, "withdrawn", "2026-03-10", "role moved to another city"),
    (7, "screening", "2026-03-16", None),
    (8, "rejected", "2026-03-19", None),
]

# (application id, follow-up date)
FOLLOW_UPS = [
    (3, "2026-03-18"),
    (4, "2026-03-06"),
    (5, "today"),
    (7, "+4d"),
    (9, "2026-03-26"),
]


def quote(arg: str) -> str:
    """Show an argument the way it would be typed in PowerShell or bash."""
    if arg == "" or any(ch in arg for ch in " ()"):
        return f'"{arg}"'
    return arg


class Demo:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.lines: list[str] = []
        self.env = {
            **os.environ,
            "JOBTRACK_DB": str(workdir / "demo.db"),
            "JOBTRACK_TODAY": DEMO_TODAY,
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONIOENCODING": "utf-8",
        }

    def say(self, text: str = "") -> None:
        self.lines.append(text)

    def heading(self, title: str) -> None:
        if self.lines and self.lines[-1] != "":
            self.say()
        self.say(f"## {title}")
        self.say()

    def run(self, *args: str, stdin: str | None = None) -> None:
        """Run `jobtrack ARGS` for real and record the command line and its output."""
        result = subprocess.run(
            [sys.executable, "-m", "jobtracker", *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            cwd=self.workdir,
            timeout=120,
        )
        shown = "jobtrack " + " ".join(quote(arg) for arg in args)
        if stdin is not None:
            shown = f"echo {stdin.strip()} | {shown}"
        self.say(f"$ {shown}")
        output = (result.stdout + result.stderr).rstrip("\n")
        if output:
            self.lines.extend(output.split("\n"))
        if result.returncode != 0:
            self.say(f"(exit code {result.returncode})")
        self.say()

    def show_file(self, name: str, first: int) -> None:
        text = (self.workdir / name).read_text(encoding="utf-8")
        self.say(f"# first {first} lines of {name}")
        self.lines.extend(text.splitlines()[:first])
        self.say()


def build_transcript() -> str:
    with tempfile.TemporaryDirectory(prefix="jobtrack-demo-") as tmp:
        demo = Demo(Path(tmp))
        demo.say("# job-tracker-cli demo")
        demo.say("# SYNTHETIC SAMPLE DATA: every company, role and link below is invented.")
        demo.say("# The database is a temporary file that is deleted when the demo ends.")
        demo.say(f'# "Today" is fixed at {DEMO_TODAY} so the output is the same on every run.')

        demo.heading("Add applications")
        for number, (company, role, location, source, applied) in enumerate(APPLICATIONS, 1):
            args = ["add", "--company", company, "--role", role, "--location", location,
                    "--source", source, "--link", f"https://example.com/jobs/{number}",
                    "--notes", NOTE]
            args += ["--date-applied", applied] if applied else ["--status", "saved"]
            demo.run(*args)

        demo.heading("Move applications along the pipeline")
        for app_id, status, on, note in STATUS_CHANGES:
            args = ["status", str(app_id), status, "--date", on]
            if note:
                args += ["--note", note]
            demo.run(*args)

        demo.heading("Set follow-up dates")
        for app_id, when in FOLLOW_UPS:
            demo.run("followup", str(app_id), when)

        demo.heading("List and filter")
        demo.run("list")
        demo.run("list", "--status", "applied,screening")
        demo.run("list", "--search", "remote", "--from", "2026-03-01")
        demo.run("list", "--company", "example", "--sort", "applied", "--desc")
        demo.run("show", "1")

        demo.heading("Follow-ups that are due")
        demo.run("due")
        demo.run("due", "--within", "7")

        demo.heading("Summary")
        demo.run("summary")
        demo.run("summary", "--from", "2026-03-01")

        demo.heading("Edit")
        demo.run("edit", "9", "--location", "Chennai (hybrid)", "--source", "")

        demo.heading("Mistakes are refused with a message")
        demo.run("add", "--company", "Typo Example Co", "--role", "Developer",
                 "--date-applied", "12/03/2026")
        demo.run("status", "4", "intervew")
        demo.run("status", "1", "applied")
        demo.run("status", "2", "interview")
        demo.run("followup", "4", "next week")
        demo.run("show", "99")

        demo.heading("Correct a wrong entry with --force")
        demo.run("status", "2", "interview", "--force", "--note", "rejection was entered by mistake")
        demo.run("show", "2")

        demo.heading("Export and import")
        demo.run("export", "applications.csv")
        demo.show_file("applications.csv", first=4)
        demo.run("export", "applications.csv")
        demo.run("--db", "second.db", "import", "applications.csv", "--dry-run")
        demo.run("--db", "second.db", "import", "applications.csv")
        demo.run("--db", "second.db", "import", "applications.csv")
        demo.run("--db", "second.db", "summary")

        demo.heading("Delete, with confirmation")
        demo.run("delete", "12", stdin="n\n")
        demo.run("delete", "12", "--yes")
        demo.run("list", "--status", "saved")

        return "\n".join(demo.lines).rstrip("\n") + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--write", action="store_true", help=f"also save the transcript to {OUTPUT_FILE.name}"
    )
    options = parser.parse_args()

    transcript = build_transcript()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(transcript)
    if options.write:
        OUTPUT_FILE.parent.mkdir(exist_ok=True)
        OUTPUT_FILE.write_text(transcript, encoding="utf-8", newline="\n")
        print(f"\n# saved to {OUTPUT_FILE.relative_to(REPO_ROOT).as_posix()}", file=sys.stderr)


if __name__ == "__main__":
    main()
