import subprocess
from pathlib import Path

import pytest

from shabbat_print.config import load_config
from shabbat_print.printer import PrintError, build_command, spool


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


CONFIGURED = '[print]\nprinter = "Some_Printer"\n'


def test_command_carries_paper_and_duplex(config, tmp_path: Path) -> None:
    command = build_command(tmp_path / "sheets.pdf", config)
    assert command[0] == "lp"
    assert "media=A4" in command
    assert "sides=two-sided-long-edge" in command
    assert "print-scaling=none" in command
    assert command[-1] == str(tmp_path / "sheets.pdf")


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
