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
