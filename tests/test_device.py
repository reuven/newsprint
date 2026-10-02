"""Turning a CUPS device URI into the printer's own IPP address.

`ippfind` is replaced by a fake runner throughout; the commands it is
handed are the real ones, checked against the Brother on 2026-10-02.
"""

import subprocess

import pytest

from newsprint.device import DeviceError, find_command, printer_uri

BROTHER = (
    "dnssd://Brother%20MFC-L2700DW%20series._ipp._tcp.local./"
    "?uuid=e3248000-80ce-11db-8000-30055cbc909c"
)


def answers(stdout: str = "", returncode: int = 0):
    """A runner that records each command and answers every one alike."""
    seen: list[list[str]] = []

    def run(command, capture_output, text, timeout):
        seen.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    return run, seen


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        ("ipp://printer.local:631/ipp/print", "ipp://printer.local:631/ipp/print"),
        ("ipps://printer.local/ipp/print", "ipps://printer.local/ipp/print"),
        # Backend options ride in the query string; the printer knows
        # nothing of them.
        (
            "ipp://printer.local/ipp/print?waitjob=false&snmp=false",
            "ipp://printer.local/ipp/print",
        ),
    ],
)
def test_an_ipp_device_is_its_own_address(device: str, expected: str) -> None:
    run, seen = answers()
    assert printer_uri(device, runner=run) == expected
    assert seen == []


@pytest.mark.parametrize(
    "device",
    [
        "usb://Brother/MFC-L2700DW%20series?serial=E73951D6N411925",
        "socket://192.168.1.20:9100",
        "lpd://printer.local/queue",
        "dnssd://Brother%20MFC-L2700DW%20series._pdl-datastream._tcp.local./",
        "dnssd://Brother%20MFC-L2700DW%20series._printer._tcp.local./",
    ],
)
def test_a_device_that_does_not_speak_ipp_has_no_printer_side_record(
    device: str,
) -> None:
    """These printers can still print; they just cannot be asked what
    they printed. None tells the caller to fall back to the CUPS count."""
    run, seen = answers()
    assert printer_uri(device, runner=run) is None
    assert seen == []


def test_a_bonjour_printer_is_found_by_its_uuid() -> None:
    """The uuid survives someone renaming the printer; the name does not."""
    run, seen = answers("ipp://BRWA8A795CD4E71.local:631/ipp/print\n")
    assert printer_uri(BROTHER, runner=run) == (
        "ipp://BRWA8A795CD4E71.local:631/ipp/print"
    )
    assert seen == [
        [
            "ippfind",
            "-T",
            "5",
            "_ipp._tcp",
            "--txt-uuid",
            "^e3248000-80ce-11db-8000-30055cbc909c$",
        ]
    ]


def test_without_a_uuid_the_exact_instance_name_is_matched() -> None:
    """ippfind's --name is a regex, so the name's own punctuation has to be
    escaped and the match anchored - "Brother (2nd floor)" must not also
    match "Brother 2nd floor" or "Old Brother (2nd floor)"."""
    command = find_command("dnssd://Brother%20(2nd%20floor)%20v1.2._ipps._tcp.local./")
    assert command == [
        "ippfind",
        "-T",
        "5",
        "_ipps._tcp",
        "--name",
        r"^Brother \(2nd floor\) v1\.2$",
    ]


def test_the_first_ipp_address_found_is_used() -> None:
    """A printer can advertise more than one; any of them reaches it."""
    run, _ = answers("\nipp://a.local:631/ipp/print\nipp://b.local:631/ipp/print\n")
    assert printer_uri(BROTHER, runner=run) == "ipp://a.local:631/ipp/print"


def test_a_bonjour_printer_that_does_not_answer_is_an_error() -> None:
    """It speaks IPP, so its record exists - not finding it means not
    knowing what printed, which must keep the mail."""
    run, _ = answers(returncode=1)
    with pytest.raises(DeviceError, match="Brother MFC-L2700DW series"):
        printer_uri(BROTHER, runner=run)


def test_an_answer_with_no_address_in_it_is_an_error() -> None:
    run, _ = answers("nothing useful\n")
    with pytest.raises(DeviceError, match="no printer answered"):
        printer_uri(BROTHER, runner=run)


def test_a_missing_ippfind_is_an_error_that_says_so() -> None:
    def run(command, capture_output, text, timeout):
        raise FileNotFoundError(command[0])

    with pytest.raises(DeviceError, match="ippfind"):
        printer_uri(BROTHER, runner=run)


def test_an_ippfind_that_hangs_is_an_error() -> None:
    def run(command, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(command, timeout)

    with pytest.raises(DeviceError, match="ippfind"):
        printer_uri(BROTHER, runner=run)


def test_ippfind_gets_a_deadline_longer_than_its_own_browse() -> None:
    timeouts: list[float] = []

    def run(command, capture_output, text, timeout):
        timeouts.append(timeout)
        return subprocess.CompletedProcess(command, 0, "ipp://a/ipp/print\n", "")

    printer_uri(BROTHER, runner=run)
    assert timeouts and timeouts[0] > 5


def test_the_real_subprocess_is_the_default(monkeypatch) -> None:
    from newsprint import device

    calls: list[list[str]] = []

    def fake_run(command, capture_output, text, timeout):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "ipp://a/ipp/print\n", "")

    monkeypatch.setattr(device.subprocess, "run", fake_run)
    assert printer_uri(BROTHER) == "ipp://a/ipp/print"
    assert calls[0][0] == "ippfind"
