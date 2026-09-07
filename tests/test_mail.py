import imaplib
from datetime import date

import pytest

from shabbat_print.mail import Mailbox, MailError, password_for

RAW = b"From: someone@example.com\r\nSubject: Hello\r\n\r\nBody.\r\n"


class FakeIMAP:
    """Records every command, so tests can assert on the exact conversation."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.calls: list[tuple] = []
        self.selected: tuple[str, bool] | None = None
        self.search_results: dict[str, bytes] = {}
        self.list_response = [
            b'(\\HasNoChildren) "/" "INBOX/toprint"',
            b'(\\HasNoChildren \\Trash) "/" "INBOX/Trash"',
        ]

    def login(self, user: str, password: str):
        self.calls.append(("login", user, password))
        return ("OK", [b"logged in"])

    def select(self, folder: str, readonly: bool = False):
        self.calls.append(("select", folder, readonly))
        self.selected = (folder, readonly)
        return ("OK", [b"2226"])

    def uid(self, command: str, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            criteria = " ".join(str(a) for a in args if a is not None)
            return ("OK", [self.search_results.get(criteria, b"")])
        if command == "FETCH":
            return ("OK", [(b"1 (RFC822 {58}", RAW), b")"])
        return ("OK", [b""])

    def list(self):
        self.calls.append(("list",))
        return ("OK", self.list_response)

    def logout(self):
        self.calls.append(("logout",))
        return ("BYE", [b"bye"])


def mailbox(fake: FakeIMAP) -> Mailbox:
    return Mailbox(
        host="imap.example.com",
        user="someone@example.com",
        password="secret",
        folder="INBOX/toprint",
        imap_factory=lambda host: fake,
    )


def test_opens_the_folder_read_only() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake):
        pass
    assert fake.selected == ("INBOX/toprint", True)
    assert ("login", "someone@example.com", "secret") in fake.calls
    assert ("logout",) in fake.calls


def test_search_flagged_returns_uids() -> None:
    fake = FakeIMAP("imap.example.com")
    fake.search_results["FLAGGED"] = b"3 17 204"
    with mailbox(fake) as box:
        assert box.search_flagged() == [3, 17, 204]


def test_search_flagged_handles_an_empty_folder() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.search_flagged() == []


def test_unflagged_since_formats_the_date_for_imap() -> None:
    fake = FakeIMAP("imap.example.com")
    fake.search_results["UNFLAGGED SINCE 01-Sep-2026"] = b"9 11"
    with mailbox(fake) as box:
        assert box.search_unflagged_since(date(2026, 9, 1)) == [9, 11]


def test_fetch_returns_the_raw_message() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.fetch(3) == RAW


def test_trash_folder_is_discovered_from_special_use() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.trash_folder() == "INBOX/Trash"


def test_trash_folder_raises_when_absent() -> None:
    fake = FakeIMAP("imap.example.com")
    fake.list_response = [b'(\\HasNoChildren) "/" "INBOX/toprint"']
    with mailbox(fake) as box, pytest.raises(MailError, match="Trash"):
        box.trash_folder()


def test_password_for_reads_the_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shabbat_print.mail.keyring.get_password", lambda host, user: "from-keychain"
    )
    assert password_for("imap.example.com", "someone@example.com") == "from-keychain"


def test_password_for_explains_how_to_store_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shabbat_print.mail.keyring.get_password", lambda host, user: None
    )
    with pytest.raises(MailError, match="keyring set"):
        password_for("imap.example.com", "someone@example.com")


# The tests above are the brief's, verbatim. The ones below exist only to
# exercise the server-error branches the brief's FakeIMAP never fails, so
# that src/ stays at 100% coverage.


class FailingIMAP:
    """Every command reports a non-OK status."""

    def __init__(self, host: str) -> None:
        self.host = host

    def login(self, user: str, password: str):
        return ("OK", [b"logged in"])

    def select(self, folder: str, readonly: bool = False):
        return ("OK", [b"0"])

    def uid(self, command: str, *args):
        return ("NO", [b"failure"])

    def list(self):
        return ("NO", [b"failure"])

    def logout(self):
        return ("BYE", [b"bye"])


class EmptyFetchIMAP(FakeIMAP):
    """FETCH reports success but the response carries no message body."""

    def uid(self, command: str, *args):
        if command == "FETCH":
            self.calls.append(("uid", command, *args))
            return ("OK", [b""])
        return super().uid(command, *args)


def test_exiting_a_mailbox_that_was_never_entered_is_a_no_op() -> None:
    """__exit__ guards on self._imap being set, in case it is ever called
    without a matching successful __enter__ - direct branch coverage for
    that guard's False arm, which a normal `with` block never exercises
    (Python only calls __exit__ after __enter__ succeeds)."""
    box = mailbox(FakeIMAP("imap.example.com"))
    box.__exit__(None, None, None)  # must not raise


def test_using_the_mailbox_before_it_is_open_raises() -> None:
    box = mailbox(FakeIMAP("imap.example.com"))
    with pytest.raises(MailError, match="not open"):
        box.search_flagged()


def test_search_raises_when_the_server_reports_failure() -> None:
    with (
        mailbox(FailingIMAP("imap.example.com")) as box,
        pytest.raises(MailError, match="search failed"),
    ):
        box.search_flagged()


def test_fetch_raises_when_the_server_reports_failure() -> None:
    with (
        mailbox(FailingIMAP("imap.example.com")) as box,
        pytest.raises(MailError, match="fetch failed"),
    ):
        box.fetch(1)


def test_fetch_raises_when_the_response_carries_no_body() -> None:
    with (
        mailbox(EmptyFetchIMAP("imap.example.com")) as box,
        pytest.raises(MailError, match="no message body"),
    ):
        box.fetch(1)


def test_trash_folder_raises_when_list_fails() -> None:
    with (
        mailbox(FailingIMAP("imap.example.com")) as box,
        pytest.raises(MailError, match="LIST failed"),
    ):
        box.trash_folder()


class LoginFailsIMAP(FakeIMAP):
    """LOGIN reports a non-OK status, as with a wrong password."""

    def login(self, user: str, password: str):
        self.calls.append(("login", user, password))
        return ("NO", [b"authentication failed"])


class SelectFailsIMAP(FakeIMAP):
    """SELECT reports a non-OK status, as with a nonexistent folder."""

    def select(self, folder: str, readonly: bool = False):
        self.calls.append(("select", folder, readonly))
        return ("NO", [b"no such mailbox"])


def test_enter_raises_when_login_fails() -> None:
    fake = LoginFailsIMAP("imap.example.com")
    with (
        pytest.raises(MailError, match="someone@example.com.*imap.example.com"),
        mailbox(fake),
    ):
        pass


def test_enter_raises_when_select_fails() -> None:
    fake = SelectFailsIMAP("imap.example.com")
    with pytest.raises(MailError, match="INBOX/toprint"), mailbox(fake):
        pass


def test_retire_moves_first_then_marks_read_and_unstars() -> None:
    """MOVE must run before either flag change.

    This is the mutation-order fix for the bug that unstarred a message
    whose MOVE then failed (an unquoted mailbox name with a space or
    brackets is rejected by the server), evaporating it from the
    star-based queue with nothing recording that it had ever been there.
    MOVE is the one irreversible act that defines "no longer queued", so
    it goes first; the flag changes afterward are best-effort tidiness for
    whichever UID now lives in Trash and cannot undo a successful move.

    This test deliberately replaces the old order-asserting test (which
    asserted +Seen, -Flagged, MOVE) rather than letting the reorder break
    it silently.
    """
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        result = box.retire([7], "INBOX/Trash")
    assert result.retired == (7,)
    assert result.failed == ()

    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    assert uid_calls == [
        ("uid", "MOVE", "7", '"INBOX/Trash"'),
        ("uid", "STORE", "7", "+FLAGS", "(\\Seen)"),
        ("uid", "STORE", "7", "-FLAGS", "(\\Flagged)"),
    ]


def test_retire_quotes_a_trash_folder_name_containing_a_space() -> None:
    """Exchange's special-use Trash is literally "Deleted Items"."""
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        box.retire([7], "Deleted Items")

    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    assert ("uid", "MOVE", "7", '"Deleted Items"') in uid_calls


def test_retire_quotes_a_trash_folder_name_containing_brackets() -> None:
    """Gmail's special-use Trash is literally "[Gmail]/Trash"."""
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        box.retire([7], "[Gmail]/Trash")

    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    assert ("uid", "MOVE", "7", '"[Gmail]/Trash"') in uid_calls


def test_quote_mailbox_escapes_embedded_quotes_and_backslashes() -> None:
    from shabbat_print.mail import _quote_mailbox

    assert _quote_mailbox('Weird"Name') == '"Weird\\"Name"'
    assert _quote_mailbox("Back\\Slash") == '"Back\\\\Slash"'


def test_retire_leaves_a_message_untouched_when_move_fails() -> None:
    """A message whose MOVE fails must remain visibly queued: still
    flagged, still unread, still in the source folder - not just present
    but silently stripped of the star that made it part of the queue."""
    fake = FakeIMAP("imap.example.com")
    original = fake.uid

    def failing(command, *args):
        if command == "MOVE":
            fake.calls.append(("uid", command, *args))
            return ("NO", [b"mailbox full"])
        return original(command, *args)

    fake.uid = failing
    with mailbox(fake) as box:
        result = box.retire([7], "INBOX/Trash")
    assert result.retired == ()
    assert result.failed == (7,)

    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    assert uid_calls == [("uid", "MOVE", "7", '"INBOX/Trash"')]


def test_retire_reopens_the_folder_writable() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        box.retire([7], "INBOX/Trash")
    assert fake.selected == ("INBOX/toprint", False)


def test_retire_reports_failures_without_stopping() -> None:
    fake = FakeIMAP("imap.example.com")

    original = fake.uid

    def failing(command, *args):
        if command == "MOVE" and args[0] == "8":
            fake.calls.append(("uid", command, *args))
            return ("NO", [b"mailbox full"])
        return original(command, *args)

    fake.uid = failing
    with mailbox(fake) as box:
        result = box.retire([7, 8, 9], "INBOX/Trash")
    assert result.retired == (7, 9)
    assert result.failed == (8,)

    # The failed message must not have had its flags touched: it stays
    # exactly as it was, still flagged and still in the source folder.
    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    assert ("uid", "STORE", "8", "+FLAGS", "(\\Seen)") not in uid_calls
    assert ("uid", "STORE", "8", "-FLAGS", "(\\Flagged)") not in uid_calls


def test_retire_raises_when_reopening_the_folder_writable_fails() -> None:
    """retire() used to discard the status from select(readonly=False)
    entirely - a non-OK status (e.g. a server refusing to reopen the
    folder writable) passed silently and every subsequent MOVE/STORE call
    would then fail confusingly, or worse, silently no-op."""

    class _NotWritableIMAP(FakeIMAP):
        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            if not readonly:
                return ("NO", [b"cannot reopen writable"])
            self.selected = (folder, readonly)
            return ("OK", [b"2226"])

    fake = _NotWritableIMAP("imap.example.com")
    with mailbox(fake) as box, pytest.raises(MailError, match="writable"):
        box.retire([7], "INBOX/Trash")


def test_retire_raises_when_the_server_reports_read_only() -> None:
    """imaplib itself raises IMAP4.readonly when a write-mode SELECT still
    reports READ-ONLY - a plain Python exception, not merely a non-OK
    status, and one retire() did not guard against at all."""

    class _RaisesReadOnlyIMAP(FakeIMAP):
        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            if not readonly:
                raise imaplib.IMAP4.readonly(f"{folder} is not writable")
            self.selected = (folder, readonly)
            return ("OK", [b"2226"])

    fake = _RaisesReadOnlyIMAP("imap.example.com")
    with mailbox(fake) as box, pytest.raises(MailError, match="not writable"):
        box.retire([7], "INBOX/Trash")


def test_retire_with_no_uids_touches_nothing() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        result = box.retire([], "INBOX/Trash")
    assert result.retired == ()
    assert result.failed == ()
    assert not [call for call in fake.calls if call[0] == "uid"]

    # The folder was never reopened writable, so nothing could have changed.
    assert fake.selected == ("INBOX/toprint", True)
