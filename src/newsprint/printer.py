"""Hand a finished document to CUPS, and wait to see what became of it.

The PDF arrives already imposed, so `lp` is asked only to select the tray and
the duplex mode. `print-scaling=none` matters: the document is laid out at
exact size and any driver-side "fit to page" would undo that.

Spooling and waiting are separate steps because they answer different
questions. `spool` says the queue took the document; only `await_completion`
says how much of it reached paper, and the caller retires mail on that
second answer rather than the first.
"""

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .cupsjob import IppError, JobState
from .cupsjob import fetch as fetch_state

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
    deliberately takes two facts to answer: CUPS ending the job normally,
    and the printer reporting at least as many sides as the document had.
    Either one alone would have called the 2026-09-18 run a success.
    """

    job: str
    state: JobState
    expected: int

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


def await_completion(
    job: str,
    expected: int,
    fetch: Callable[[int], JobState] = fetch_state,
    sleep: Callable[[float], None] = time.sleep,
    interval: float = POLL_SECONDS,
    on_progress: Callable[[JobState], None] | None = None,
) -> PrintOutcome:
    """Poll until the job stops, and report what it managed.

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
            return PrintOutcome(job=job, state=state, expected=expected)
        sleep(interval)
