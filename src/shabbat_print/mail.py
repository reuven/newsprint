"""IMAP access to the print queue.

The folder is opened read-only. Everything that can crash - HTML parsing,
rendering, imposition - runs while this connection has no power to modify
mail; the read-write connection is opened later, by retire(), and only after a
job has reached the print queue.
"""

import imaplib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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


def _message_count(data: Sequence[bytes | None]) -> int:
    """The folder's EXISTS count, already carried in the SELECT response.

    imaplib's own select() hands back this count as data[0] - a bytes
    string of digits, straight off the server's "* <n> EXISTS" line (RFC
    3501) - so reading it here costs nothing extra: no second round trip
    (e.g. STATUS) is needed just to report how many messages a folder
    holds. Falls back to 0 for a well-formed OK reply that nonetheless
    does not carry a parseable count, rather than crashing an otherwise
    successful SELECT over a cosmetic number.
    """
    try:
        return int(data[0])
    except IndexError, TypeError, ValueError:
        return 0


def _quote_mailbox(name: str) -> str:
    """IMAP-quote a mailbox name for use as a raw command argument.

    Nothing in imaplib quotes a mailbox name for the caller - not
    IMAP4.select(), not IMAP4.copy(), and not IMAP4.uid(); every argument is
    sent exactly as given. A name containing an atom-breaking character (a
    space, or square brackets) must be quoted by hand or the server answers
    NO/BAD. Gmail's own \\Trash folder is literally "[Gmail]/Trash";
    Exchange's is literally "Deleted Items" - both break MOVE unquoted.
    """
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def password_for(host: str, user: str) -> str:
    secret = keyring.get_password(host, user)
    if not secret:
        raise MailError(
            f"no password in the keychain for {user} at {host}. "
            f"Store it once with:  keyring set {host} {user}"
        )
    return secret


@dataclass(frozen=True, slots=True)
class RetireResult:
    retired: tuple[int, ...]
    failed: tuple[int, ...]
    # A uid in `failed` whose rescue re-star (after a failed MOVE) also
    # failed: read, unstarred, and stuck in the source folder, gone from
    # the star-based queue with nothing visible to the user. Always a
    # subset of `failed`. The one state the user cannot discover on their
    # own, so it is named separately rather than folded into an ordinary
    # failure.
    unrecoverable: tuple[int, ...] = ()


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
        # How many messages the folder holds - set by __enter__ from the
        # SELECT response itself, so a caller reporting progress can show
        # it without a second round trip. 0 until the folder is opened.
        self.message_count: int = 0

    def __enter__(self) -> Self:
        imap = self._factory(self._host)
        status, _ = imap.login(self._user, self._password)
        if status != "OK":
            raise MailError(f"login failed for {self._user} at {self._host}: {status}")
        status, data = imap.select(self._folder, readonly=True)
        if status != "OK":
            raise MailError(f"could not open folder {self._folder!r}: {status}")
        self._imap = imap
        self.message_count = _message_count(data)
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

    def retire(self, uids: Sequence[int], trash_folder: str) -> RetireResult:
        """Mark each message read and unstar it, then move it to Trash.

        Called only after a job has reached the print queue, and only for the
        messages whose content actually reached it.

        The two STORE calls run *before* MOVE, not after. RFC 6851: a
        successful MOVE expunges the source UID from the selected folder;
        RFC 3501 says a subsequent command naming an expunged UID is
        ignored. A version of this method that ran MOVE first and STORE
        afterward therefore issued two commands that a real server
        silently no-ops - dead code that let mail land in Trash still
        starred and unread despite a fully green test suite, because the
        fake in use at the time had no notion of expunge and answered OK
        to everything. `FakeIMAP` now tracks moved UIDs and answers NO to
        any later command against one, so this order is the one under
        which the user's stated requirement - read, unstarred, *and*
        moved - can actually be met.

        MOVE is still the one irreversible act that defines "no longer
        queued": if it fails, the message has already been marked read
        and unstarred, so it must be re-starred (`+FLAGS \\Flagged`) or it
        would silently vanish from the star-based queue while remaining
        stuck, unmoved, in the source folder - the Critical finding's
        invariant, approached from the other direction. `\\Seen` is
        deliberately never rolled back: whether the message was already
        read before this run cannot be known, and it genuinely was
        printed, so leaving it read matches intent. If the rescue re-star
        itself fails, the uid is recorded in `unrecoverable` as well as
        `failed` - that combination is the one state the user cannot
        discover on their own.
        """
        if not uids:
            return RetireResult(retired=(), failed=())

        connection = self._connection
        try:
            status, _ = connection.select(self._folder, readonly=False)
        except imaplib.IMAP4.error as error:
            raise MailError(
                f"could not reopen {self._folder!r} writable: {error}"
            ) from error
        if status != "OK":
            raise MailError(f"could not reopen {self._folder!r} writable: {status}")

        retired: list[int] = []
        failed: list[int] = []
        unrecoverable: list[int] = []
        for uid in uids:
            identifier = str(uid)
            connection.uid("STORE", identifier, "+FLAGS", "(\\Seen)")
            connection.uid("STORE", identifier, "-FLAGS", "(\\Flagged)")
            status, _ = connection.uid("MOVE", identifier, _quote_mailbox(trash_folder))
            if status != "OK":
                failed.append(uid)
                restore_status, _ = connection.uid(
                    "STORE", identifier, "+FLAGS", "(\\Flagged)"
                )
                if restore_status != "OK":
                    unrecoverable.append(uid)
                continue
            retired.append(uid)
        return RetireResult(
            retired=tuple(retired),
            failed=tuple(failed),
            unrecoverable=tuple(unrecoverable),
        )
