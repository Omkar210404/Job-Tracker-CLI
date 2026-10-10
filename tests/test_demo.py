"""The demo script, the committed transcript and the README must agree.

The demo runs the real CLI in separate processes, so this is also the
end-to-end test of the whole program.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO = REPO_ROOT / "scripts" / "demo.py"
TRANSCRIPT = REPO_ROOT / "examples" / "demo_output.txt"
README = REPO_ROOT / "README.md"


@pytest.fixture(scope="module")
def fresh_run(tmp_path_factory):
    """Run the demo once, with the home folder pointed at an empty temp folder."""
    home = tmp_path_factory.mktemp("home")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    env.pop("JOBTRACK_DB", None)
    result = subprocess.run(
        [sys.executable, str(DEMO)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=home,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout, home


def test_demo_output_is_labelled_synthetic(fresh_run):
    output, _ = fresh_run
    assert "SYNTHETIC SAMPLE DATA" in output.splitlines()[1]
    assert output.count("https://example.com/jobs/") >= 12


def test_demo_never_touches_the_default_database(fresh_run):
    _, home = fresh_run
    assert not (home / ".jobtrack").exists()
    assert list(home.iterdir()) == []


def test_committed_transcript_matches_a_fresh_run(fresh_run):
    output, _ = fresh_run
    committed = TRANSCRIPT.read_text(encoding="utf-8")
    assert output == committed, (
        "examples/demo_output.txt is out of date. "
        "Run: python scripts/demo.py --write"
    )


def test_demo_shows_refusals_without_tracebacks(fresh_run):
    output, _ = fresh_run
    assert "Traceback" not in output
    assert output.count("(exit code 1)") == 7
    assert "(exit code 2)" not in output


def test_summary_in_the_transcript_adds_up(fresh_run):
    """The counts printed by `summary` must match the rows printed by `list`."""
    output, _ = fresh_run
    listing = output.split("$ jobtrack list\n", 1)[1].split("\n\n", 1)[0].splitlines()[2:]
    statuses = [re.split(r"\s{2,}", row.strip())[3] for row in listing]
    summary = output.split("$ jobtrack summary\n", 1)[1].split("Response rate", 1)[0]
    counts = dict(re.findall(r"^(\w+)\s+(\d+)$", summary, flags=re.MULTILINE))
    assert len(statuses) == 12
    for status, count in counts.items():
        assert statuses.count(status) == int(count), status
    assert sum(int(count) for count in counts.values()) == 12


def console_blocks(markdown: str) -> list[str]:
    return re.findall(r"```console\n(.*?)```", markdown, flags=re.DOTALL)


def test_readme_examples_are_copied_from_the_transcript():
    """Every ```console block in the README must appear verbatim in the transcript."""
    if not README.exists():
        pytest.skip("README not written yet")
    transcript = TRANSCRIPT.read_text(encoding="utf-8")
    blocks = console_blocks(README.read_text(encoding="utf-8"))
    assert blocks, "the README should show real output in ```console blocks"
    for block in blocks:
        assert block.strip("\n") in transcript, f"README example not found in transcript:\n{block}"
