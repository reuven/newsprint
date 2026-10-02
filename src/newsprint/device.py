"""Find the printer behind a CUPS queue, so it can be asked what it printed.

CUPS knows a queue's printer only as a device URI, and on macOS that is
usually a Bonjour name - `dnssd://Brother%20MFC-L2700DW%20series._ipp._tcp
.local./?uuid=...` - rather than anything that can be sent a request.
`ippfind`, which ships with CUPS on macOS and in cups-ipp-utils on most
Linux distributions, resolves it to the printer's own IPP address.

Three answers, and the difference between the last two matters:

- an `ipp://` address, for any printer that keeps its own job records;
- `None`, for a device that does not speak IPP at all (USB, a raw socket,
  LPD). There is nothing to ask, so the caller falls back to CUPS's count;
- `DeviceError`, for an IPP printer that could not be found. Its record
  exists and could not be read, which is "unknown", never "fine".
"""

import re
import subprocess
import urllib.parse
from collections.abc import Callable

_IPP_SCHEMES = frozenset({"ipp", "ipps"})
_IPP_SERVICES = ("_ipp._tcp", "_ipps._tcp")

# ippfind browses for this long before giving up, and the subprocess gets
# a little longer than that before it is presumed hung.
_BROWSE_SECONDS = 5
_DEADLINE_SECONDS = _BROWSE_SECONDS + 10

# POSIX extended regex metacharacters, which ippfind's --name uses.
_REGEX_SPECIAL = re.compile(r"([.\[\]()*+?{}|^$\\])")

Runner = Callable[..., subprocess.CompletedProcess[str]]


class DeviceError(Exception):
    """The queue's printer speaks IPP but could not be reached."""


def _service(device: str) -> tuple[str, str] | None:
    """(instance name, service type) out of a dnssd:// URI, if it names an
    IPP service. Other Bonjour services - raw socket, LPD - return None."""
    host = urllib.parse.unquote(urllib.parse.urlsplit(device).netloc)
    for service in _IPP_SERVICES:
        instance, found, _ = host.partition(f".{service}.")
        if found:
            return instance, service
    return None


def find_command(device: str) -> list[str] | None:
    """The ippfind command that resolves a dnssd:// URI, or None if the URI
    is not a Bonjour IPP printer.

    The uuid is preferred because it survives someone renaming the
    printer. Without one, the instance name is matched exactly: --name
    takes a regex, so it is escaped and anchored.
    """
    found = _service(device)
    if found is None:
        return None
    instance, service = found
    command = ["ippfind", "-T", str(_BROWSE_SECONDS), service]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(device).query)
    if "uuid" in query:
        return [*command, "--txt-uuid", _exactly(query["uuid"][0])]
    return [*command, "--name", _exactly(instance)]


def _exactly(text: str) -> str:
    """An anchored POSIX regex matching `text` and nothing else. Python's
    re.escape will not do: it escapes "-", which POSIX leaves undefined."""
    return "^" + _REGEX_SPECIAL.sub(r"\\\1", text) + "$"


def printer_uri(device: str, runner: Runner | None = None) -> str | None:
    """The printer's IPP address for a CUPS device URI. See the module
    docstring for what None and DeviceError each mean."""
    parts = urllib.parse.urlsplit(device)
    if parts.scheme in _IPP_SCHEMES:
        return urllib.parse.urlunsplit(parts._replace(query="", fragment=""))
    if parts.scheme != "dnssd":
        return None
    command = find_command(device)
    if command is None:
        return None

    run = runner or subprocess.run
    name = urllib.parse.unquote(parts.netloc).split("._")[0]
    try:
        result = run(command, capture_output=True, text=True, timeout=_DEADLINE_SECONDS)
    except FileNotFoundError as error:
        raise DeviceError(
            "ippfind is not installed, so the printer cannot be asked what it "
            "printed (on Linux it is in the cups-ipp-utils package)"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise DeviceError(f"ippfind did not finish looking for {name}") from error
    if result.returncode != 0:
        raise DeviceError(f"{name} did not answer on the network")
    for line in result.stdout.splitlines():
        if urllib.parse.urlsplit(line.strip()).scheme in _IPP_SCHEMES:
            return line.strip()
    raise DeviceError(f"no printer answered as {name}")
