"""IMAP access to the print queue.

The folder is opened read-only. Everything that can crash - HTML parsing,
rendering, imposition - runs while this connection has no power to modify
mail; the read-write connection is opened later, by retire(), and only after a
job has reached the print queue.
"""

import imaplib
import re
from collections.abc import Callable
from datetime import date
from types import TracebackType
from typing import Self

import keyring

IMAPFactory = Callable[[str], imaplib.IMAP4]

_TRASH_LINE = re.compile(rb'\\Trash\b[^"]*"[^"]*"\s+"?([^"]+?)"?\s*$')

# strftime's %b reads LC_TIME, so a non-English locale can render a SEARCH
# date the server rejects: de_DE appends a period ("Sep."), fr_FR uses its
# own abbreviation ("sept."), he_IL spells the month in Hebrew entirely.
# RFC 3501 wants the fixed English three-letter form regardless of locale,
# so it is spelled out here instead of asking strftime for it. Do not
# "simplify" this back to strftime.
_IMAP_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class MailError(Exception):
    """Something went wrong talking to the mail server."""


def password_for(host: str, user: str) -> str:
    secret = keyring.get_password(host, user)
    if not secret:
        raise MailError(
            f"no password in the keychain for {user} at {host}. "
            f"Store it once with:  keyring set {host} {user}"
        )
    return secret


class Mailbox:
    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        folder: str,
        imap_factory: IMAPFactory = imaplib.IMAP4_SSL,
    ) -> None:
        self._host = host
        self._user = user
        self._password = password
        self._folder = folder
        self._factory = imap_factory
        self._imap: imaplib.IMAP4 | None = None

    def __enter__(self) -> Self:
        imap = self._factory(self._host)
        status, _ = imap.login(self._user, self._password)
        if status != "OK":
            raise MailError(f"login failed for {self._user} at {self._host}: {status}")
        status, _ = imap.select(self._folder, readonly=True)
        if status != "OK":
            raise MailError(f"could not open folder {self._folder!r}: {status}")
        self._imap = imap
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            finally:
                self._imap = None

    @property
    def _connection(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise MailError("mailbox is not open; use it as a context manager")
        return self._imap

    def _search(self, criteria: str) -> list[int]:
        status, data = self._connection.uid("SEARCH", None, criteria)
        if status != "OK":
            raise MailError(f"search failed for {criteria!r}: {status}")
        payload = data[0] or b""
        return [int(uid) for uid in payload.split()]

    def search_flagged(self) -> list[int]:
        return self._search("FLAGGED")

    def search_unflagged_since(self, since: date) -> list[int]:
        month = _IMAP_MONTHS[since.month - 1]
        return self._search(f"UNFLAGGED SINCE {since.day:02d}-{month}-{since.year}")

    def fetch(self, uid: int) -> bytes:
        status, data = self._connection.uid("FETCH", str(uid), "(RFC822)")
        if status != "OK":
            raise MailError(f"fetch failed for uid {uid}: {status}")
        for item in data:
            if isinstance(item, tuple) and len(item) > 1:
                return item[1]
        raise MailError(f"fetch returned no message body for uid {uid}")

    def trash_folder(self) -> str:
        status, lines = self._connection.list()
        if status != "OK":
            raise MailError(f"LIST failed: {status}")
        for line in lines:
            match = _TRASH_LINE.search(line)
            if match:
                return match.group(1).decode()
        raise MailError("no folder advertises the \\Trash special-use attribute")
