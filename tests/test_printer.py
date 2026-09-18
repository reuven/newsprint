import subprocess
from pathlib import Path

import pytest

from newsprint.config import load_config
from newsprint.cupsjob import IppError, JobState
from newsprint.printer import (
    PrintError,
    await_completion,
    build_command,
    job_number,
    spool,
)


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


CONFIGURED = '[print]\nprinter = "Some_Printer"\n'


def test_command_carries_paper_and_duplex(config, tmp_path: Path) -> None:
    """The whole command, in order. Each option has to be introduced by
    its own -o: lp takes "-o name=value" pairs, and an option that
    arrives without one is not an option - print-scaling=none in
    particular, which is what stops the printer shrinking the sheet to
    its own margins and putting every cell in the wrong place.
    """
    command = build_command(tmp_path / "sheets.pdf", config)
    assert command == [
        "lp",
        "-o",
        "media=A4",
        "-o",
        "sides=two-sided-long-edge",
        "-o",
        "print-scaling=none",
        str(tmp_path / "sheets.pdf"),
    ]


def test_an_unset_printer_uses_the_system_default(config, tmp_path: Path) -> None:
    """No -d at all, so CUPS picks its own default destination."""
    assert "-d" not in build_command(tmp_path / "sheets.pdf", config)


def test_a_configured_printer_is_named(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIGURED)
    command = build_command(tmp_path / "sheets.pdf", load_config(path))
    assert command[:3] == ["lp", "-d", "Some_Printer"]


def test_letter_override_reaches_the_command(tmp_path: Path) -> None:
    config = load_config(tmp_path / "absent.toml", paper_override="letter")
    assert "media=Letter" in build_command(tmp_path / "sheets.pdf", config)


def test_spool_returns_the_job_id(config, tmp_path: Path) -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="request id is Some_Printer-137 (1 file(s))\n",
            stderr="",
        )

    assert spool(tmp_path / "sheets.pdf", config, runner=runner) == "Some_Printer-137"


def test_spool_raises_on_failure(config, tmp_path: Path) -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="lp: no such printer"
        )

    with pytest.raises(PrintError, match="no such printer"):
        spool(tmp_path / "sheets.pdf", config, runner=runner)


def test_spool_raises_when_the_job_id_is_missing(config, tmp_path: Path) -> None:
    """A zero exit with unparseable output must not be reported as success."""

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout="something else\n", stderr=""
        )

    with pytest.raises(PrintError, match="job id"):
        spool(tmp_path / "sheets.pdf", config, runner=runner)


def test_spool_hands_lp_the_command_and_reads_what_it_says(config, tmp_path) -> None:
    """The job id comes back on lp's stdout, so the call has to capture
    it and has to ask for text rather than bytes - a run that captured
    nothing would find no id in None and report a failure for a job that
    reached the queue perfectly well."""
    calls: list[tuple[object, dict[str, object]]] = []

    def recording_runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "request id is HP-42\n", "")

    pdf = tmp_path / "sheets.pdf"
    assert spool(pdf, config, runner=recording_runner) == "HP-42"
    assert calls == [
        (build_command(pdf, config), {"capture_output": True, "text": True})
    ]


# --- waiting for the job to actually finish -------------------------------


def state(job_state: int, impressions: int) -> JobState:
    return JobState(
        state=job_state, reasons=(), impressions=impressions, sheets=impressions // 2
    )


def polls(*states: JobState):
    """A fetch that walks a scripted sequence, then repeats the last one."""
    remaining = list(states)

    def fetch(job_id: int) -> JobState:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return fetch


def test_job_number_reads_the_id_lp_printed() -> None:
    """lp names a job "<destination>-<number>", and the destination is
    free to contain digits and hyphens of its own."""
    assert job_number("Brother_MFC_L2700DW_series-8727") == 8727


def test_job_number_rejects_something_unnumbered() -> None:
    with pytest.raises(PrintError, match="job number"):
        job_number("Brother_MFC_L2700DW_series")


def test_waiting_returns_once_the_job_stops() -> None:
    outcome = await_completion(
        "P-1",
        107,
        fetch=polls(state(5, 20), state(5, 80), state(9, 107)),
        sleep=lambda _: None,
    )
    assert outcome.complete is True
    assert outcome.state.impressions == 107


def test_a_short_print_is_not_complete_even_though_cups_says_completed() -> None:
    """The 2026-09-18 failure, in one assertion: state 9, 67 of 107 sides."""
    outcome = await_completion(
        "P-1", 107, fetch=polls(state(9, 67)), sleep=lambda _: None
    )
    assert outcome.state.succeeded is True
    assert outcome.complete is False
    assert (outcome.state.impressions, outcome.expected) == (67, 107)


def test_an_aborted_job_is_not_complete() -> None:
    outcome = await_completion("P-1", 4, fetch=polls(state(8, 4)), sleep=lambda _: None)
    assert outcome.complete is False


def test_more_impressions_than_expected_still_counts_as_complete() -> None:
    """A banner page or a driver that counts differently must not be read
    as a failure - the question is whether anything is missing."""
    outcome = await_completion(
        "P-1", 107, fetch=polls(state(9, 108)), sleep=lambda _: None
    )
    assert outcome.complete is True


def test_progress_is_reported_on_every_poll() -> None:
    seen: list[int] = []
    await_completion(
        "P-1",
        107,
        fetch=polls(state(5, 20), state(5, 80), state(9, 107)),
        sleep=lambda _: None,
        on_progress=lambda job_state: seen.append(job_state.impressions),
    )
    assert seen == [20, 80, 107]


def test_it_waits_between_polls_but_not_after_the_last_one() -> None:
    """Sleeping after the job has ended would add the poll interval to
    every single run for nothing."""
    slept: list[float] = []
    await_completion(
        "P-1",
        107,
        fetch=polls(state(5, 20), state(9, 107)),
        sleep=slept.append,
        interval=2.5,
    )
    assert slept == [2.5]


def test_an_unreachable_cups_is_a_print_error() -> None:
    """Not knowing how the job ended has to stop the run: the caller
    retires mail on the strength of this answer."""

    def fetch(job_id: int) -> JobState:
        raise IppError("could not reach CUPS")

    with pytest.raises(PrintError, match="could not reach CUPS"):
        await_completion("P-1", 107, fetch=fetch, sleep=lambda _: None)


def test_the_job_number_is_what_gets_polled() -> None:
    """lp hands back a name; CUPS answers to the number inside it. Polling
    anything else would ask about a job that is not this one."""
    asked: list[int] = []

    def fetch(job_id: int) -> JobState:
        asked.append(job_id)
        return state(9, 4)

    await_completion(
        "Brother_MFC_L2700DW_series-8727", 4, fetch=fetch, sleep=lambda _: None
    )
    assert asked == [8727]


def test_the_outcome_remembers_which_job_it_describes() -> None:
    """The run log and the error message both name the job, and a wrong
    name there sends someone to the wrong entry in lpstat."""
    outcome = await_completion(
        "Printer-9", 4, fetch=polls(state(9, 4)), sleep=lambda _: None
    )
    assert outcome.job == "Printer-9"
