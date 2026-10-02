import subprocess
from pathlib import Path

import pytest

from newsprint.config import load_config
from newsprint.cupsjob import IppError, JobState, PrinterJob
from newsprint.device import DeviceError
from newsprint.printer import (
    PrintError,
    await_completion,
    build_command,
    job_number,
    locate_printer,
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


# --- following the job onto the printer -----------------------------------
#
# CUPS counts a side once it has sent it. On 2026-10-02 it said 66 of 116
# while the printer, out of memory, had cancelled the job at 60. So once
# CUPS lets go, the printer's own record decides.

PRINTER = "ipp://printer.local:631/ipp/print"
PACKET = "packet-2026-10-02-1227.pdf"


def on_printer(*snapshots: list[PrinterJob]):
    """A printer job list that walks a scripted sequence, then repeats."""
    remaining = list(snapshots)
    asked: list[str] = []

    def jobs(uri: str) -> list[PrinterJob]:
        asked.append(uri)
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return jobs, asked


def printer_job(number: int, job_state: int, impressions: int, name: str = PACKET):
    return PrinterJob(number, name, JobState(job_state, (), impressions, 0))


def test_the_printers_count_decides_not_cupss() -> None:
    """The 2026-10-02 run: CUPS sent 66 and called it completed; the
    printer cancelled at 60. The outcome is the printer's."""
    canceled = PrinterJob(
        10210, PACKET, JobState(7, ("job-canceled-at-device",), 60, 0)
    )
    jobs, asked = on_printer([canceled])
    outcome = await_completion(
        "P-8732",
        116,
        fetch=polls(state(9, 66)),
        sleep=lambda _: None,
        document=PACKET,
        printer=PRINTER,
        jobs=jobs,
    )
    assert outcome.complete is False
    assert outcome.confirmed is True
    assert outcome.state.impressions == 60
    assert outcome.state.reasons == ("job-canceled-at-device",)
    assert outcome.sent == 66
    assert outcome.printer_job == 10210
    assert outcome.shortfall == 56
    assert outcome.job == "P-8732"
    assert asked == [PRINTER]


def test_a_job_cups_has_finished_sending_is_followed_until_it_prints() -> None:
    """CUPS finishing is not paper finishing: the last few sheets are still
    in the printer, and could still jam."""
    jobs, _ = on_printer(
        [printer_job(10, 5, 110)], [printer_job(10, 5, 114)], [printer_job(10, 9, 116)]
    )
    printed: list[int] = []
    slept: list[float] = []
    outcome = await_completion(
        "P-1",
        116,
        fetch=polls(state(9, 116)),
        sleep=slept.append,
        interval=2.0,
        document=PACKET,
        printer=PRINTER,
        jobs=jobs,
        on_printed=lambda job_state: printed.append(job_state.impressions),
    )
    assert outcome.complete is True
    assert printed == [110, 114, 116]
    assert slept == [2.0, 2.0]


def test_the_newest_job_of_that_name_is_the_one() -> None:
    """A reprint of the same packet carries the same name; the newest job
    id is this run's."""
    jobs, _ = on_printer(
        [
            printer_job(10210, 7, 60),
            printer_job(10212, 9, 116),
            printer_job(10211, 9, 4, name="something-else.pdf"),
        ]
    )
    outcome = await_completion(
        "P-1",
        116,
        fetch=polls(state(9, 116)),
        sleep=lambda _: None,
        document=PACKET,
        printer=PRINTER,
        jobs=jobs,
    )
    assert outcome.printer_job == 10212
    assert outcome.complete is True


def test_a_printer_with_no_record_of_the_job_is_a_print_error() -> None:
    """The Brother keeps one finished job. If another has finished since,
    this one's count is gone - which is "unknown", and keeps the mail."""
    jobs, _ = on_printer([printer_job(10213, 9, 2, name="someone-elses.pdf")])
    with pytest.raises(PrintError, match="no record of " + PACKET):
        await_completion(
            "P-1",
            116,
            fetch=polls(state(9, 116)),
            sleep=lambda _: None,
            document=PACKET,
            printer=PRINTER,
            jobs=jobs,
        )


def test_a_printer_that_stops_answering_is_a_print_error() -> None:
    def jobs(uri: str) -> list[PrinterJob]:
        raise IppError("could not reach the printer")

    with pytest.raises(PrintError, match="about job P-1: could not reach the printer"):
        await_completion(
            "P-1",
            116,
            fetch=polls(state(9, 116)),
            sleep=lambda _: None,
            document=PACKET,
            printer=PRINTER,
            jobs=jobs,
        )


def test_without_a_printer_record_the_cups_count_stands_unconfirmed() -> None:
    """A USB printer keeps no job records. CUPS's count is all there is,
    and the outcome says so rather than pretending otherwise."""

    def jobs(uri: str) -> list[PrinterJob]:
        raise AssertionError("there is no printer to ask")

    outcome = await_completion(
        "P-1", 4, fetch=polls(state(9, 4)), sleep=lambda _: None, jobs=jobs
    )
    assert outcome.complete is True
    assert outcome.confirmed is False
    assert outcome.printer_job is None
    assert outcome.sent == 4


def test_the_printer_is_asked_only_once_cups_has_let_go() -> None:
    """Until then the printer's count can only lag CUPS's, and asking
    costs a request to a printer that is busy printing."""
    order: list[str] = []

    def fetch(job_id: int) -> JobState:
        order.append("cups")
        return state(5, 1) if order.count("cups") == 1 else state(9, 2)

    def jobs(uri: str) -> list[PrinterJob]:
        order.append("printer")
        return [printer_job(1, 9, 2)]

    await_completion(
        "P-1",
        2,
        fetch=fetch,
        sleep=lambda _: None,
        document=PACKET,
        printer=PRINTER,
        jobs=jobs,
    )
    assert order == ["cups", "cups", "printer"]


# --- finding the printer ---------------------------------------------------


def test_the_override_wins_without_asking_anyone() -> None:
    def device(queue: str) -> str:
        raise AssertionError("discovery should not run")

    assert locate_printer("Office-12", override=PRINTER, device=device) == PRINTER


def test_the_queue_is_read_off_the_job_and_its_device_resolved() -> None:
    """lp names a job "<queue>-<number>", and the queue may contain
    hyphens and digits of its own."""
    asked: list[str] = []

    def device(queue: str) -> str:
        asked.append(queue)
        return "dnssd://X._ipp._tcp.local./"

    def resolve(uri: str) -> str | None:
        assert uri == "dnssd://X._ipp._tcp.local./"
        return PRINTER

    assert (
        locate_printer("Brother-2-floor-8732", device=device, resolve=resolve)
        == PRINTER
    )
    assert asked == ["Brother-2-floor"]


def test_a_device_with_no_printer_side_record_locates_to_none() -> None:
    assert (
        locate_printer(
            "Q-1", device=lambda queue: "usb://B/X", resolve=lambda uri: None
        )
        is None
    )


@pytest.mark.parametrize(
    "failure", [IppError("cupsd is down"), DeviceError("ippfind is not installed")]
)
def test_failing_to_find_an_ipp_printer_is_a_print_error(failure: Exception) -> None:
    def device(queue: str) -> str:
        raise failure

    with pytest.raises(PrintError, match=str(failure)):
        locate_printer("Q-1", device=device)

    def resolve(uri: str) -> str | None:
        raise failure

    with pytest.raises(PrintError, match="Q"):
        locate_printer("Q-1", device=lambda queue: "dnssd://x", resolve=resolve)
