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

# A UID FETCH response line always carries the message's own UID as a
# data item (RFC 3501), regardless of what else was asked for - which is
# how _parse_fetch_response maps a response tuple back to its uid, rather
# than assuming the server answers in request order (it is not required
# to, and measured against the user's real server it does not always).
_FETCH_UID_RE = re.compile(rb"UID (\d+)")

# Measured against the user's real server: 40 single-message FETCH round
# trips took 6.95s; one batched UID FETCH for the same 40 took 0.17s - the
# cost is per-command latency, not bandwidth, so batching is the whole
# fix. Chunking on top of that is a safety net, not a further speed-up:
# nothing in RFC 3501 caps a command line's length, but real servers do
# (Dovecot's default is 64KiB; older or more conservative ones can be far
# smaller) - 200 uids, each at most a handful of digits plus a comma,
# stays comfortably under any of them while still being one round trip
# for every ordinary run (the live queue's largest fetch, ~98 unstarred
# candidates, is one chunk).
FETCH_CHUNK_SIZE = 200

# What a server closing the connection under us actually looks like from
# inside imaplib: a write to the closed socket raises BrokenPipeError (an
# OSError), and once the SSL layer notices, ssl.SSLError (also an
# OSError) or imaplib.IMAP4.abort. None of them arrive as a NO status, so
# checking the status a fetch returns can never see them. IMAP4.error is
# deliberately not here: it covers protocol-level failures on a
# connection that is still alive, which a reconnect would not fix.
_DROPPED_CONNECTION = (imaplib.IMAP4.abort, OSError)

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


_SIZE_RE = re.compile(rb"UID (\d+) RFC822\.SIZE (\d+)")


def _parse_size_response(
    data: Sequence[bytes | tuple[bytes, bytes] | None],
) -> dict[int, int]:
    """Turn a batched `UID FETCH <uid-set> (UID RFC822.SIZE)` response
    into {uid: size}.

    Unlike a body fetch, RFC822.SIZE carries no literal - each matched
    message is one self-contained bytes line (e.g. b"171 (UID 28310
    RFC822.SIZE 44796)"), not a (info, payload) tuple, so this reads
    plain bytes items directly rather than reusing
    _parse_fetch_response's tuple-unpacking.
    """
    result: dict[int, int] = {}
    for item in data:
        if not isinstance(item, bytes):
            continue
        match = _SIZE_RE.search(item)
        if match:
            result[int(match.group(1))] = int(match.group(2))
    return result


def _parse_fetch_response(
    data: Sequence[bytes | tuple[bytes, bytes] | None],
) -> dict[int, bytes]:
    """Turn a (possibly multi-message) UID FETCH response into {uid:
    payload}.

    Each matched message contributes one (info, payload) tuple to `data`,
    followed by a closing b')' entry that carries no data of its own -
    the exact same shape imaplib returns for a single-uid fetch, just
    repeated once per message for a batched one. The uid is read from
    each tuple's own info line rather than from its position in `data`,
    because a real server is not required to (and, measured against the
    user's own server, does not always) answer a UID FETCH in the order
    the UID set was requested in.
    """
    result: dict[int, bytes] = {}
    for item in data:
        if not (isinstance(item, tuple) and len(item) > 1):
            continue
        info, payload = item[0], item[1]
        match = _FETCH_UID_RE.search(info)
        if match:
            result[int(match.group(1))] = payload
    return result


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
        # The folder's UIDVALIDITY as of the current connection, so a
        # reconnect can prove the uids this run is holding still name the
        # same messages. Set by _open().
        self._uidvalidity: bytes | None = None

    def __enter__(self) -> Self:
        self._imap = self._open()
        return self

    def _open(self) -> imaplib.IMAP4:
        imap = self._factory(self._host)
        status, _ = imap.login(self._user, self._password)
        if status != "OK":
            raise MailError(f"login failed for {self._user} at {self._host}: {status}")
        status, data = imap.select(self._folder, readonly=True)
        if status != "OK":
            raise MailError(f"could not open folder {self._folder!r}: {status}")
        self.message_count = _message_count(data)
        self._uidvalidity = imap.response("UIDVALIDITY")[1][0]
        return imap

    def _reconnect(self) -> None:
        """Replace a connection the server has closed under us.

        The read-only connection is held open across the picker prompt,
        which is an unbounded human pause - the user reads a scrollable
        list of a hundred newsletters and picks a few. A server is free
        to close an idle connection while that happens (RFC 3501 permits
        it after 30 minutes; real servers are often far less patient),
        and it closes it silently: the next write fails with
        BrokenPipeError, or the SSL layer raises IMAP4.abort, from inside
        imaplib. Reconnecting costs one connect/login/select and is
        invisible to the caller.

        Only read-only fetches retry through here. Nothing in the
        retirement path does: STORE and MOVE change server state, and a
        blind retry of a command that may already have been applied is
        how mail gets lost.
        """
        previous = self._uidvalidity
        # _connection, not _imap: _reconnect is only ever reached from a
        # command that just used the connection, so it cannot be None
        # here - and if that ever changes, the property says so plainly
        # rather than silently skipping the close.
        try:
            self._connection.shutdown()
        except imaplib.IMAP4.error, OSError:
            # The socket this is closing is the one that just failed, so
            # closing it failing too is the expected case, not an error.
            pass
        self._imap = None
        self._imap = self._open()
        if self._uidvalidity != previous:
            raise MailError(
                "the folder was renumbered while this run was in progress "
                f"(UIDVALIDITY {previous!r} -> {self._uidvalidity!r}); "
                "the messages this run selected can no longer be identified"
            )

    def _fetch(self, uid_set: str, items: str) -> tuple[str, list]:
        """One UID FETCH, retried once on a dropped connection.

        A FETCH is read-only and idempotent, so re-issuing it after a
        reconnect returns the same messages or fails loudly - it cannot
        double-apply anything. Exactly one retry: a server that keeps
        dropping should surface as a failed run, not a reconnect loop.
        """
        try:
            return self._connection.uid("FETCH", uid_set, items)
        except _DROPPED_CONNECTION:
            self._reconnect()
        return self._connection.uid("FETCH", uid_set, items)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except imaplib.IMAP4.error, OSError:
                # Closing a connection the server has already closed is a
                # no-op, not a failure - and raising here would replace
                # whatever real error sent us out of the block (in the
                # user's crash report, a BrokenPipeError from a FETCH)
                # with a second, less informative traceback.
                pass
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

    def fetch_many(
        self, uids: Sequence[int], items: str = "(UID RFC822)"
    ) -> dict[int, bytes]:
        """Fetch every uid in `uids` in as few round trips as possible.

        One `UID FETCH <comma-joined uid set> <items>` command covers an
        entire chunk (see FETCH_CHUNK_SIZE) - a 40x speed-up over fetching
        one message at a time, measured against the user's real server
        (6.95s -> 0.17s for 40 headers). `items` lets a caller ask for
        only what it needs - the default is a full message, but the
        unstarred picker asks for headers only, via
        BODY.PEEK[HEADER.FIELDS (...)] - PEEK specifically, so scanning
        a week's unstarred mail never marks any of it \\Seen.

        Raises if the server reports failure for a chunk, or if any
        requested uid never appears in what came back - a message the
        server silently skipped is not the same as one it truthfully
        fetched, and must not be undercounted without saying so.
        """
        if not uids:
            return {}
        result: dict[int, bytes] = {}
        for start in range(0, len(uids), FETCH_CHUNK_SIZE):
            chunk = uids[start : start + FETCH_CHUNK_SIZE]
            uid_set = ",".join(str(uid) for uid in chunk)
            status, data = self._fetch(uid_set, items)
            if status != "OK":
                raise MailError(f"fetch failed for {len(chunk)} uid(s): {status}")
            result.update(_parse_fetch_response(data))
        missing = [uid for uid in uids if uid not in result]
        if missing:
            raise MailError(f"fetch returned no message body for uid(s): {missing}")
        return result

    def fetch_sizes(self, uids: Sequence[int]) -> dict[int, int]:
        """RFC822.SIZE for every uid in `uids`, batched like fetch_many.

        Measured against the live queue: 0.18s for 99 candidates, versus
        11.8s for their full bodies - the picker's length indicator reads
        this, not an exact word count, so scanning a large unstarred
        window for lengths costs about as much as the header scan
        fetch_unstarred already pays. No PEEK is needed: RFC822.SIZE is
        server-side metadata the SELECT/EXAMINE state already carries -
        unlike BODY[...], it never touches (or fetches) the message body,
        so it cannot set \\Seen.

        Raises if the server reports failure for a chunk, or if any
        requested uid never appears in what came back, the same
        undercounting guarantee fetch_many gives for bodies.
        """
        if not uids:
            return {}
        result: dict[int, int] = {}
        for start in range(0, len(uids), FETCH_CHUNK_SIZE):
            chunk = uids[start : start + FETCH_CHUNK_SIZE]
            uid_set = ",".join(str(uid) for uid in chunk)
            status, data = self._fetch(uid_set, "(UID RFC822.SIZE)")
            if status != "OK":
                raise MailError(f"fetch failed for {len(chunk)} uid(s): {status}")
            result.update(_parse_size_response(data))
        missing = [uid for uid in uids if uid not in result]
        if missing:
            raise MailError(f"fetch returned no size for uid(s): {missing}")
        return result

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
