"""Hand a finished document to CUPS, and wait to see what became of it.

The PDF arrives already imposed, so `lp` is asked only to select the tray and
the duplex mode. `print-scaling=none` matters: the document is laid out at
exact size and any driver-side "fit to page" would undo that.

Spooling and waiting are separate steps because they answer different
questions. `spool` says the queue took the document; only `await_completion`
says how much of it reached paper, and the caller retires mail on that
second answer rather than the first.

"Reached paper" is the printer's answer, not CUPS's. CUPS counts a side
once it has sent it, so on 2026-10-02 it reported 66 of 116 sides while
the printer - out of memory - had cancelled the job at 60. Waiting
therefore has two stages: CUPS until it has sent everything, then the
printer's own job record until the printer stops too.
"""

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .cupsjob import IppError, JobState, PrinterJob, printer_jobs
from .cupsjob import device_uri as queue_device
from .cupsjob import fetch as fetch_state
from .device import DeviceError
from .device import printer_uri as resolve_device

_JOB_ID = re.compile(r"request id is (\S+)")
_JOB_NUMBER = re.compile(r"-(\d+)$")

POLL_SECONDS = 2.0

Runner = Callable[..., subprocess.CompletedProcess[str]]


class PrintError(Exception):
    """The job did not reach the print queue, or its fate could not be read.

    Both are failures for the same reason: the caller must not retire mail
    on an outcome nobody established.
    """


def build_command(pdf: Path, config: Config) -> list[str]:
    command = ["lp"]
    if config.printing.printer:
        command += ["-d", config.printing.printer]
    # With no -d, CUPS sends the job to its own default destination, which is
    # the right behavior for anyone who has not named a printer.
    command += [
        "-o",
        f"media={config.printing.paper.name}",
        "-o",
        f"sides={config.printing.duplex}",
        "-o",
        "print-scaling=none",
        str(pdf),
    ]
    return command


def spool(pdf: Path, config: Config, runner: Runner = subprocess.run) -> str:
    command = build_command(pdf, config)
    result = runner(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise PrintError(result.stderr.strip() or f"lp exited {result.returncode}")
    match = _JOB_ID.search(result.stdout)
    if match is None:
        raise PrintError(
            f"lp reported no job id; output was: {result.stdout.strip()!r}"
        )
    return match.group(1)


@dataclass(frozen=True, slots=True)
class PrintOutcome:
    """How a job ended, against how much of it there was to print.

    `complete` is the only question the caller needs answered, and it
    deliberately takes two facts to answer: the job ending normally, and
    at least as many sides as the document had. Either one alone would
    have called the 2026-09-18 run a success.

    `state` is the printer's own record when `confirmed`, and CUPS's when
    the printer keeps none (USB and the like). `sent` is always CUPS's
    count - what left the computer, which is not what reached paper.
    """

    job: str
    state: JobState
    expected: int
    sent: int = 0
    printer_job: int | None = None

    @property
    def confirmed(self) -> bool:
        """The counts came from the printer, not from CUPS."""
        return self.printer_job is not None

    @property
    def complete(self) -> bool:
        return self.state.succeeded and self.state.impressions >= self.expected

    @property
    def shortfall(self) -> int:
        return max(0, self.expected - self.state.impressions)


def job_number(job: str) -> int:
    """The numeric id out of lp's "<destination>-<number>".

    Destinations contain hyphens and digits of their own, so this anchors
    on the last hyphen rather than splitting on every one.
    """
    match = _JOB_NUMBER.search(job)
    if match is None:
        raise PrintError(f"could not read a job number from {job!r}")
    return int(match.group(1))


def locate_printer(
    job: str,
    override: str = "",
    device: Callable[[str], str] = queue_device,
    resolve: Callable[[str], str | None] = resolve_device,
) -> str | None:
    """The IPP address of the printer `job` went to, or None if that
    printer keeps no job records of its own.

    Called before waiting rather than after: discovery takes seconds, and
    a printer can forget a finished job as soon as another one finishes.
    """
    if override:
        return override
    queue = job.rsplit("-", 1)[0]
    try:
        return resolve(device(queue))
    except (IppError, DeviceError) as error:
        raise PrintError(
            f"could not find the printer behind queue {queue}: {error}"
        ) from error


def await_completion(
    job: str,
    expected: int,
    fetch: Callable[[int], JobState] = fetch_state,
    sleep: Callable[[float], None] = time.sleep,
    interval: float = POLL_SECONDS,
    on_progress: Callable[[JobState], None] | None = None,
    *,
    document: str = "",
    printer: str | None = None,
    jobs: Callable[[str], list[PrinterJob]] = printer_jobs,
    on_printed: Callable[[JobState], None] | None = None,
) -> PrintOutcome:
    """Poll until the job stops, and report what it managed.

    `on_progress` hears CUPS's count of sides sent; `on_printed` hears the
    printer's count of sides printed, once CUPS has sent them all. With no
    `printer` there is no second stage and CUPS's count stands.

    The wait is deliberately unbounded: a printer that is switched off or
    out of paper will finish the job when someone attends to it, and
    giving up early would hand back exactly the uncertainty this function
    exists to remove. Ctrl-C is the escape, and it is safe - the caller
    has not retired anything yet.

    A failure to read the state is raised rather than swallowed, because
    "I could not tell" and "it printed" must not lead to the same place.
    """
    number = job_number(job)
    while True:
        try:
            state = fetch(number)
        except IppError as error:
            raise PrintError(
                f"could not read the state of job {job}: {error}"
            ) from error
        if on_progress is not None:
            on_progress(state)
        if state.terminal:
            break
        sleep(interval)

    if printer is None:
        return PrintOutcome(
            job=job, state=state, expected=expected, sent=state.impressions
        )
    return _follow_on_printer(
        job,
        expected,
        state.impressions,
        document,
        printer,
        jobs,
        sleep,
        interval,
        on_printed,
    )


def _follow_on_printer(
    job: str,
    expected: int,
    sent: int,
    document: str,
    printer: str,
    jobs: Callable[[str], list[PrinterJob]],
    sleep: Callable[[float], None],
    interval: float,
    on_printed: Callable[[JobState], None] | None,
) -> PrintOutcome:
    """Poll the printer's record of `document` until it stops.

    Matched by name, newest id first: a reprint of the same packet shares
    the name, and the newest is this run's. No record at all means the
    printer has already forgotten it, and how much printed is unknown.
    """
    while True:
        try:
            mine = [found for found in jobs(printer) if found.name == document]
        except IppError as error:
            raise PrintError(
                f"could not ask the printer at {printer} about job {job}: {error}"
            ) from error
        if not mine:
            raise PrintError(
                f"the printer at {printer} has no record of {document}, "
                "so how much of it reached paper is unknown"
            )
        newest = max(mine, key=lambda found: found.id)
        if on_printed is not None:
            on_printed(newest.state)
        if newest.state.terminal:
            return PrintOutcome(
                job=job,
                state=newest.state,
                expected=expected,
                sent=sent,
                printer_job=newest.id,
            )
        sleep(interval)
