# PLAN — job-tracker-cli

A Python command-line tool to track job applications, stored locally in one SQLite file.

Status: **approved 2026-10-10**, with the planned defaults in section 12.

## 1. Environment (inspected 2026-10-10)

| Item | Found |
|------|-------|
| OS / shell | Windows 11, PowerShell |
| Python | 3.11.9 (Microsoft Store build, no `py` launcher) |
| SQLite (stdlib `sqlite3`) | 3.45.1 |
| Global packages | `typer` and `pytest` **not** installed |
| Internet / PyPI | reachable (latest listed: typer 0.27.3, pytest 9.1.1) |
| Git | 2.49.0, identity `Omkar210404 <omkarnikamofficial21@gmail.com>`, sibling repos use `main` |
| GitHub CLI (`gh`) | not installed, so the GitHub repo is created and pushed by hand |
| GPU | not needed |

Consequence: everything runs in a project-local `.venv` (git-ignored). `requirements.txt` pins the exact versions that install and pass the tests.

## 2. Scope

In: one user, one local SQLite file, a CLI to add, update, list, summarise, export and import applications.
Out: no web UI, no accounts, no sync, no scraping of job sites, no email or calendar integration, no reminders that fire on their own (the `due` command has to be run).

## 3. Commands

Invoked as `jobtrack <command>` after `pip install -e .`, or `python -m jobtracker <command>` without installing.

| Command | What it does |
|---------|--------------|
| `add` | Add an application. `--company` and `--role` required; `--link`, `--location`, `--source`, `--notes`, `--date-applied` (default today), `--status saved\|applied` (default `applied`; `saved` stores no applied date) |
| `list` | Table of applications. Filters combine with AND: `--status` (repeatable), `--company` (substring), `--from` / `--to` (applied date range), `--search` (text in company, role, location, source, notes), `--sort`, `--limit` |
| `show ID` | All fields of one application plus its status history |
| `status ID NEW_STATUS` | Move along the pipeline. `--date` (default today), `--note`, `--force` to override the rules when correcting a mistake |
| `followup ID DATE` | Set the follow-up date. `--clear` removes it |
| `due` | Applications whose follow-up date is today or earlier, marked `due today` or `overdue by N days`. `--within N` also shows the next N days |
| `edit ID` | Change any field except status (`--company`, `--role`, `--link`, `--location`, `--source`, `--notes`, `--date-applied`) |
| `delete ID` | Shows the application, asks `Delete? [y/N]`, then deletes. `--yes` skips the question |
| `summary` | Counts per status, response rate, average days from applied to first response. `--from` / `--to` limit it by applied date |
| `export PATH` | Write all applications to a CSV file. `--overwrite` replaces an existing file |
| `import PATH` | Read applications from a CSV file. `--dry-run` validates without writing |

Global options: `--db PATH` (or env `JOBTRACK_DB`), `--version`, `--help` on every command.

Dates are typed as `YYYY-MM-DD` or `today`; the follow-up date also accepts `+7d` (N days from today).

Exit codes: `0` success, `1` the request was understood but refused (unknown id, invalid date, status move not allowed, bad CSV), `2` usage error. Errors print one line starting `Error:` on stderr, never a traceback.

## 4. Status rules

Pipeline order: `saved` → `applied` → `screening` → `interview` → `offer`. Closed states: `rejected`, `withdrawn`.

| From | Allowed without `--force` |
|------|---------------------------|
| `saved` | `applied`, `withdrawn` |
| `applied` | `screening`, `interview`, `offer`, `rejected`, `withdrawn` |
| `screening` | `interview`, `offer`, `rejected`, `withdrawn` |
| `interview` | `offer`, `rejected`, `withdrawn` |
| `offer` | `rejected`, `withdrawn` |
| `rejected`, `withdrawn` | nothing |

- Forward moves may skip stages (`applied` → `interview`), because real processes do.
- Moving backwards, leaving a closed state, or anything else outside the table needs `--force`. It exists to fix a wrong entry, and the move is still written to the history.
- Moving to the status it already has is an error.
- `saved` → `applied` sets the applied date to the change date if it is empty.
- A status change dated before the applied date is refused.

## 5. Data model

Database file, schema version kept in `PRAGMA user_version` (starts at 1). `PRAGMA foreign_keys = ON` on every connection.

**applications**

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | ids are never reused after a delete |
| `company` | TEXT NOT NULL | not blank |
| `role` | TEXT NOT NULL | not blank |
| `link`, `location`, `source`, `notes` | TEXT | optional |
| `status` | TEXT NOT NULL | CHECK: one of the seven statuses |
| `date_applied` | TEXT | ISO date; empty while `saved` |
| `follow_up_date` | TEXT | ISO date or empty |
| `first_response_date` | TEXT | ISO date or empty; maintained by the service layer, see below |
| `created_at`, `updated_at` | TEXT | ISO timestamps |

**status_history** (one row per status change, shown by `show`)

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PRIMARY KEY | |
| `application_id` | INTEGER NOT NULL | FOREIGN KEY → applications, ON DELETE CASCADE |
| `old_status` | TEXT | empty for the first entry |
| `new_status` | TEXT NOT NULL | |
| `changed_on` | TEXT NOT NULL | ISO date |
| `note` | TEXT | |

Dates are stored as ISO text, so they sort and compare correctly as strings.

### Summary definitions

Written here so the numbers mean one thing, and repeated in the README.

- **Response statuses**: `screening`, `interview`, `offer`, `rejected`. `withdrawn` is the user's own action and is not a response.
- **`first_response_date`**: set the first time an application enters a response status, to the date of that change. Never overwritten by later changes. Cleared only by a forced move back to `saved` or `applied`.
- **Applied** = applications with an applied date. `saved` ones are not counted.
- **Responded** = applied applications that have a `first_response_date` or currently sit in a response status.
- **Response rate** = responded ÷ applied, shown with both counts (`5 of 12`). Shown as `n/a` when nothing has been applied.
- **Average days to first response** = mean of (`first_response_date` − `date_applied`) over responded applications that have both dates, shown with how many it is based on. `n/a` when there are none.

No benchmark, target or "typical" figures appear anywhere. Every number comes from the user's own rows.

### Follow-up rules

- `due` lists applications with a follow-up date on or before today, oldest first.
- Applications in `rejected` or `withdrawn` are left out of `due`. Their follow-up date is kept, not erased.
- "Today" is passed into the service layer as an argument, so tests fix the date instead of depending on the clock. The CLI reads the system date, or `JOBTRACK_TODAY` when set (used by the demo so its output is reproducible).

### CSV

- Columns: `id, company, role, link, location, source, status, date_applied, follow_up_date, first_response_date, notes`.
- Export writes UTF-8 with a header row. Import reads `utf-8-sig`, so a file re-saved by Excel still opens.
- Import needs `company` and `role`; other columns are optional. `status` defaults to `applied`. The `id` column is ignored and new ids are assigned.
- Every row is validated first. If any row is invalid, each problem is listed with its line number and **nothing** is imported.
- A row matching an existing application on company + role + applied date is skipped and counted, so importing the same file twice does not double the data.
- Status history is not part of the CSV. An imported application starts with one history entry.

## 6. Structure

Three layers, each depending only on the one below it.

```
job-tracker-cli/
├── jobtracker/
│   ├── __init__.py          # version
│   ├── __main__.py          # python -m jobtracker
│   ├── models.py            # Status enum, Application / StatusChange / Summary dataclasses
│   ├── errors.py            # JobTrackerError, ValidationError, NotFoundError
│   ├── db.py                # DATABASE LAYER: connect, create schema, SQL for insert/get/update/delete/query
│   ├── validation.py        # parse dates, statuses, required text
│   ├── status_rules.py      # allowed transitions (pure functions, no database)
│   ├── service.py           # SERVICE LAYER: add, edit, delete, change status, follow-ups, due list, filters, summary
│   ├── csv_io.py            # export / import, built on the service layer
│   ├── formatting.py        # plain-text tables and summary text (ASCII only)
│   └── cli.py               # CLI LAYER: Typer app, turns errors into messages and exit codes
├── scripts/demo.py          # fills a temporary database with synthetic data and runs real commands
├── examples/demo_output.txt # committed output of a real demo run, source of the README examples
├── tests/
├── .github/workflows/tests.yml
├── README.md  PLAN.md  LICENSE  .gitignore  .gitattributes  pyproject.toml  requirements.txt
```

- `db.py` knows SQL and nothing about rules. `service.py` knows the rules and never prints. `cli.py` parses arguments, calls the service, prints.
- `pyproject.toml` is minimal: package metadata and the `jobtrack` console script. `requirements.txt` holds the pinned versions.
- `.gitignore`: `*.db`, `*.sqlite3`, `*.db-journal`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, exported `*.csv` at the repo root, `.env`.

## 7. Demo script

`python scripts/demo.py`:

1. Creates a database in a temporary folder, never the real one.
2. Sets a fixed "today" so due and overdue output is the same on every run.
3. Runs the real CLI as a subprocess for each step (add, status changes, follow-ups, list with filters, due, summary, export) and prints each command with its output.
4. `--write` saves that transcript to `examples/demo_output.txt`.

About 12 invented applications. Labelled synthetic in the script docstring, in a banner at the top of the output, in the README, and in the data itself: made-up company names, `https://example.com/...` links, and a `synthetic sample` note on every row. No real companies or people.

README examples are copied from `examples/demo_output.txt`. A test re-runs the demo and fails if the committed file or the README blocks no longer match.

## 8. Tests (pytest, temporary database via `tmp_path`)

| Module | Covers |
|--------|--------|
| `test_db.py` | schema created on a new file and left alone on an existing one; insert / get / update / delete; CHECK refuses an unknown status; deleting an application removes its history; filter queries; ids not reused |
| `test_validation.py` | valid and invalid dates (`2026-02-30`, `10/03/2026`, empty), `today`, `+7d`, unknown status with the list of valid ones in the message, blank company or role |
| `test_status_rules.py` | every pair of the 7×7 transition table, parametrised; skipping stages; closed states; `--force`; same status |
| `test_status_changes.py` | the rules applied to stored applications: applied date set on `saved` → `applied`; change dated before the applied date; `first_response_date` set once, not overwritten, cleared on a forced move back; history entries |
| `test_followup.py` | set, change, clear; due today, overdue by N, tomorrow not due; `--within`; closed applications left out; ordering |
| `test_summary.py` | empty database gives zeros and `n/a`, no division error; counts per status including zeros; response rate and average days checked against hand-worked values; `saved` not counted; withdrawn not a response; date range |
| `test_service.py` | add, edit, delete, unknown id, list filters alone and combined |
| `test_csv.py` | export then import into an empty database gives the same rows; invalid rows reported with line numbers and nothing written; duplicates skipped; missing header; `--dry-run` |
| `test_cli.py` | Typer's test runner against a temporary database: each command, help text, delete answered yes / no / `--yes`, errors give exit 1 and no traceback |
| `test_demo.py` | demo runs in a temp folder, the real database path is never touched, committed transcript and README blocks match a fresh run |

Pass counts in the README and final report will be copied from the actual pytest run.

## 9. CI

`.github/workflows/tests.yml`: on every push and pull request, install `requirements.txt` and the package, run `pytest`. Matrix: `ubuntu-latest` and `windows-latest`, Python 3.11 and 3.13.

Badge in the README points at that workflow. It can only show a result after the repo is on GitHub, so I will not describe CI as passing until a real run has finished there.

## 10. Commits

Small, one concern each, existing git identity, no `Co-Authored-By` or session lines.

1. Add PLAN.md, LICENSE, .gitignore, requirements.txt, pyproject.toml
2. Add models, errors and validation with tests
3. Add database layer with tests
4. Add status rules with tests
5. Add service layer: add, edit, delete, list filters
6. Add status changes and history to the service
7. Add follow-up dates and due list
8. Add summary
9. Add CSV export and import
10. Add CLI and plain-text formatting with CLI tests
11. Add demo script and committed demo output
12. Add GitHub Actions workflow
13. Add README with real output, structure, limitations

## 11. Risks and how they are handled

| Risk | Handling |
|------|----------|
| Typer 0.27 is new to this machine; its Rich help boxes change with terminal width and would make README output unstable | Use plain help and plain error output; print tables with our own ASCII formatter. If Typer cannot be made plain and stable, fall back to `argparse` and say so |
| Output depends on today's date | Date injected into the service; `JOBTRACK_TODAY` for the demo |
| SQLite ignores foreign keys by default | `PRAGMA foreign_keys = ON` on connect, with a test for cascade delete |
| "Response rate" and "days to first response" can be defined several ways | One written definition (section 5), counts shown beside every ratio, hand-worked test values |
| Imported rows without a first response date | Counted as responded if their status says so, left out of the average, and the average states how many rows it used |
| Excel rewrites dates as `DD/MM/YYYY` when a CSV is re-saved | Import accepts ISO dates only and names the line and value; stated in limitations |
| Delete cannot be undone | Confirmation showing what will be deleted; `--yes` is explicit; README suggests `export` as a backup |
| Windows console encoding (cp1252) breaks on box characters | ASCII-only output |
| Temporary database files stay locked on Windows | Connections closed in fixtures and in the CLI |
| Line endings differ between Windows and the Linux CI runner | `.gitattributes` forces LF on the committed transcript; comparison normalises newlines |
| CI cannot be checked before the first push | Workflow kept simple; tests run locally on Windows first; result reported only from a real run |
| A later schema change breaks existing databases | `PRAGMA user_version` recorded now; migrations are a future idea, not built |
| Cells starting with `=` in an exported CSV are treated as formulas by spreadsheets | Stated as a limitation; the data is the user's own |

## 12. Decisions (approved as planned)

1. **Typer**, not argparse (typed options, good help text, built-in test runner; sap-data-cleaner already shows argparse). Cost: one runtime dependency.
2. **Default database location**: `~/.jobtrack/jobtrack.db`, so the tool works from any folder and the real data sits outside the repo. `--db` or `JOBTRACK_DB` overrides it, and `*.db` is git-ignored either way.
3. **GitHub repo name for the CI badge**: `Omkar210404/Job-Tracker-CLI`, following `Sap-Data-Cleaner` and `News-Lens`.
4. **Status strictness**: the table in section 4, with `--force` for corrections.

## 13. Changes made while building

Small points decided during the build, all inside the approved plan:

- **Future dates are refused** for the applied date and for a status change. A follow-up date may be any date.
- **A forced move back to `saved` also clears the applied date**, so "saved has no applied date" stays true for the summary.
- **`withdrawn` may have no applied date** (a saved job dropped before applying). Such an application is not counted as applied.
- **`export` refuses to overwrite an existing file** unless `--overwrite` is given.
- **`list --status` accepts commas** (`--status applied,screening`) as well as repeating the option.
- **CSV header names are forgiving** about case, spaces and hyphens (`Date Applied` is read as `date_applied`). Unknown columns are an error, to catch typos.
- **Service-level status tests live in `tests/test_status_changes.py`**; `tests/test_status_rules.py` holds the pure transition table.
- **History dates are not checked against each other**, only against the applied date and today. An ordering check would have made `--force` corrections impossible. Listed as a limitation.
- **Typer 0.27 could be made plain** (`rich_markup_mode=None`), so the argparse fallback was not needed.

## 14. Deviations from brain.md

- No `.env.example`: no secrets or API keys.
- No screenshots: CLI only, so the README shows real terminal output.
- A small Mermaid diagram of the three layers goes in the README.
- This project is outside the three listed in `brain.md`. `brain.md`, `prompts/` and the other project folders are not touched and stay outside this repo.
