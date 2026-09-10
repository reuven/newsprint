"""Hand a finished document to CUPS.

The PDF arrives already imposed, so `lp` is asked only to select the tray and
the duplex mode. `print-scaling=none` matters: the document is laid out at
exact size and any driver-side "fit to page" would undo that.
"""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .config import Config

_JOB_ID = re.compile(r"request id is (\S+)")

Runner = Callable[..., subprocess.CompletedProcess[str]]


class PrintError(Exception):
    """The job did not reach the print queue."""


def build_command(pdf: Path, config: Config) -> list[str]:
    command = ["lp"]
    if config.printing.printer:
        command += ["-d", config.printing.printer]
    # With no -d, CUPS sends the job to its own default destination, which is
    # the right behaviour for anyone who has not named a printer.
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
