import subprocess
from pathlib import Path

import pytest

from newsprint.config import load_config
from newsprint.printer import PrintError, build_command, spool


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
