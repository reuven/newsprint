import imaplib
from datetime import date

import pytest

from newsprint.mail import (
    FETCH_CHUNK_SIZE,
    Mailbox,
    MailError,
    UnretireResult,
    password_for,
)

RAW = b"From: someone@example.com\r\nSubject: Hello\r\n\r\nBody.\r\n"


class FakeIMAP:
    """Records every command, so tests can assert on the exact conversation.

    Also models RFC 6851 expunge-on-MOVE semantics: a successful MOVE
    removes the source UID from the selected folder, so any later UID
    STORE or UID MOVE naming that same UID is answered NO, the way a real
    server silently rejects a command against a message that is no longer
    there (RFC 3501). This is not decorative: the dead-code version of
    retire() issued its two STORE calls *after* a successful MOVE, and the
    old FakeIMAP - which had no notion of expunge at all - answered OK to
    both, so a fully green suite shipped a bug where mail landed in Trash
    still starred and unread. Without this, no fake could ever catch that
    class of bug again.
    """

    def __init__(self, host: str) -> None:
        self.host = host
        self.calls: list[tuple] = []
        self.selected: tuple[str, bool] | None = None
        self.search_results: dict[str, bytes] = {}
        self.expunged: set[str] = set()
        # RFC822.SIZE per uid, for fetch_sizes() - a uid absent here is
        # simply omitted from the response, the way a real server would
        # answer for a uid it does not have.
        self.sizes: dict[int, int] = {}
        # UIDVALIDITY is what a reconnect compares to decide whether the
        # uids it is still holding mean anything on the new connection.
        self.uidvalidity = b"1"
        # What the server advertises. MOVE (RFC 6851) and UIDPLUS (RFC
        # 4315) are both extensions; retirement falls back when either is
        # missing, so a test can drop them from here to exercise that.
        self.capabilities = ("IMAP4REV1", "MOVE", "UIDPLUS")
        self.logout_error: BaseException | None = None
        self.shutdown_error: BaseException | None = None
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
        if command in ("STORE", "MOVE") and args and args[0] in self.expunged:
            return ("NO", [b"invalid UID: message has been expunged"])
        if command == "SEARCH":
            criteria = " ".join(str(a) for a in args if a is not None)
            return ("OK", [self.search_results.get(criteria, b"")])
        if command == "FETCH":
            items = args[1] if len(args) > 1 else ""
            if "RFC822.SIZE" in items:
                # RFC822.SIZE carries no literal - unlike a body fetch,
                # each matched message is one self-contained bytes line,
                # e.g. b"171 (UID 28310 RFC822.SIZE 44796)", not a
                # (info, payload) tuple. Real batched UID FETCH response,
                # measured against the user's own server. A comma-joined
                # uid set is handled directly here (unlike the literal
                # FETCH branch below, which MultiFetchIMAP must override
                # to batch) because there is no literal-payload bookkeeping
                # to get right.
                uid_arg = args[0] if args else "1"
                requested = [int(token) for token in str(uid_arg).split(",")]
                data = [
                    f"1 (UID {uid} RFC822.SIZE {self.sizes[uid]})".encode()
                    for uid in requested
                    if uid in self.sizes
                ]
                return ("OK", data)
            # A real server's UID FETCH response always carries the
            # message's own UID as a data item (RFC 3501) - not just its
            # sequence number - which is how Mailbox.fetch_many() maps a
            # response tuple back to the uid it belongs to. args[0] here
            # is the uid set string a single-uid fetch() call sends.
            uid_arg = args[0] if args else "1"
            info = f"{uid_arg} (UID {uid_arg} RFC822 {{58}}".encode()
            return ("OK", [(info, RAW), b")"])
        if command == "MOVE":
            self.expunged.add(args[0])
        return ("OK", [b""])

    def list(self):
        self.calls.append(("list",))
        return ("OK", self.list_response)

    def response(self, code: str):
        self.calls.append(("response", code))
        return (code, [self.uidvalidity])

    def shutdown(self):
        self.calls.append(("shutdown",))
        if self.shutdown_error is not None:
            raise self.shutdown_error

    def logout(self):
        self.calls.append(("logout",))
        if self.logout_error is not None:
            raise self.logout_error
        return ("BYE", [b"bye"])


class MultiFetchIMAP(FakeIMAP):
    """Models a real server's response to a batched `UID FETCH
    <uid-set> <items>` covering several messages: one (info, payload)
    tuple per matched message, each followed by a closing b')' entry -
    the same per-message shape FakeIMAP's own single-uid FETCH already
    uses, just repeated once per message. Per RFC 3501, a UID FETCH
    response always carries the message's own UID as a data item, which
    is what `fetch_many()` reads to map a response tuple back to its uid
    - never response position, since a server is free to answer in a
    different order than the UID set was requested in.

    `messages` maps a uid to the raw bytes the fake should hand back for
    it; a uid with no entry is simply omitted from the response, the way
    a server would if asked for a uid it does not have. `response_order`,
    when set, controls the order those uids appear in the response,
    independent of request order, for the out-of-order test.
    """

    def __init__(self, host: str) -> None:
        super().__init__(host)
        self.messages: dict[int, bytes] = {}
        self.response_order: list[int] | None = None
        self.fetch_calls: list[str] = []

    def uid(self, command: str, *args):
        if command == "FETCH":
            self.calls.append(("uid", command, *args))
            uid_set, items = args[0], args[1]
            self.fetch_calls.append(uid_set)
            requested = [int(token) for token in uid_set.split(",")]
            order = (
                self.response_order if self.response_order is not None else requested
            )
            data: list[bytes | tuple[bytes, bytes]] = []
            for uid in order:
                if uid not in requested:
                    continue
                payload = self.messages.get(uid)
                if payload is None:
                    continue
                info = f"{uid} (UID {uid} {items} {{{len(payload)}}}".encode()
                data.append((info, payload))
                data.append(b")")
            return ("OK", data)
        return super().uid(command, *args)


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


def test_fetch_many_returns_the_raw_messages_keyed_by_uid() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.fetch_many([3]) == {3: RAW}


def test_fetch_many_with_no_uids_makes_no_call() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.fetch_many([]) == {}
    assert not [call for call in fake.calls if call[0] == "uid"]


# ---------------------------------------------------------------------------
# fetch_sizes - RFC822.SIZE, for the picker's length indicator
# ---------------------------------------------------------------------------


def test_fetch_sizes_returns_byte_sizes_keyed_by_uid() -> None:
    fake = FakeIMAP("imap.example.com")
    fake.sizes = {3: 44796, 17: 14996}
    with mailbox(fake) as box:
        assert box.fetch_sizes([3, 17]) == {3: 44796, 17: 14996}


def test_fetch_sizes_issues_one_uid_fetch_for_the_whole_set() -> None:
    """The whole point, same as fetch_many: one round trip, not one per
    uid - a comma-joined UID set in a single UID FETCH command."""
    fake = FakeIMAP("imap.example.com")
    fake.sizes = {3: 100, 17: 200, 204: 300}
    with mailbox(fake) as box:
        result = box.fetch_sizes([3, 17, 204])
    assert result == {3: 100, 17: 200, 204: 300}
    fetch_calls = [
        call for call in fake.calls if call[0] == "uid" and call[1] == "FETCH"
    ]
    assert len(fetch_calls) == 1
    assert fetch_calls[0][2] == "3,17,204"


def test_fetch_sizes_never_sets_the_seen_flag() -> None:
    """RFC822.SIZE is server metadata, not a body fetch - it never needs
    (and never uses) BODY.PEEK, because it never touches the body at
    all. This asserts the exact items string sent, so a future change
    that swaps in a body-fetch item by mistake cannot pass silently."""
    fake = FakeIMAP("imap.example.com")
    fake.sizes = {3: 100}
    with mailbox(fake) as box:
        box.fetch_sizes([3])
    fetch_calls = [
        call for call in fake.calls if call[0] == "uid" and call[1] == "FETCH"
    ]
    assert fetch_calls[0][3] == "(UID RFC822.SIZE)"


def test_fetch_sizes_with_no_uids_makes_no_call() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.fetch_sizes([]) == {}
    assert not [call for call in fake.calls if call[0] == "uid"]


def test_fetch_sizes_raises_on_a_uid_the_server_omitted() -> None:
    """A uid the server silently skipped must not be undercounted without
    saying so - the same guarantee fetch_many gives for bodies."""
    fake = FakeIMAP("imap.example.com")
    fake.sizes = {3: 100}
    with mailbox(fake) as box, pytest.raises(MailError, match=r"\[17\]"):
        box.fetch_sizes([3, 17])


def test_fetch_sizes_chunks_above_fetch_chunk_size() -> None:
    fake = FakeIMAP("imap.example.com")
    uids = list(range(1, FETCH_CHUNK_SIZE + 21))
    fake.sizes = {uid: uid * 10 for uid in uids}
    with mailbox(fake) as box:
        result = box.fetch_sizes(uids)
    assert result == {uid: uid * 10 for uid in uids}
    fetch_calls = [
        call for call in fake.calls if call[0] == "uid" and call[1] == "FETCH"
    ]
    assert len(fetch_calls) == 2


def test_fetch_sizes_raises_when_the_server_reports_failure() -> None:
    with (
        mailbox(FailingIMAP("imap.example.com")) as box,
        pytest.raises(MailError, match="fetch failed"),
    ):
        box.fetch_sizes([1])


def test_parse_size_response_ignores_a_non_bytes_item() -> None:
    """A SIZE fetch response never carries a (info, payload) tuple - see
    _parse_size_response's own docstring - but the parser must not crash
    if one ever showed up (e.g. a server that echoed a literal anyway)."""
    from newsprint.mail import _parse_size_response

    mixed = [(b"1 (RFC822 {5}", b"hello"), b"2 (UID 3 RFC822.SIZE 100)"]
    assert _parse_size_response(mixed) == {3: 100}


def test_parse_size_response_ignores_a_line_with_no_size_data_item() -> None:
    """A malformed or unrelated FETCH response line must not be
    attributed to any uid, the same as a server that never answered for
    it at all - mirrors _parse_fetch_response's own guarantee."""
    from newsprint.mail import _parse_size_response

    malformed = [b"1 (FLAGS (\\Seen))"]
    assert _parse_size_response(malformed) == {}


def test_fetch_many_issues_one_uid_fetch_for_the_whole_set() -> None:
    """The whole point: 40 uids in one round trip, not 40. A comma-joined
    UID set in a single UID FETCH command, not a loop of single fetches."""
    fake = MultiFetchIMAP("imap.example.com")
    fake.messages = {3: RAW, 17: RAW, 204: b"From: x\r\nSubject: y\r\n\r\nZ\r\n"}
    with mailbox(fake) as box:
        result = box.fetch_many([3, 17, 204])
    assert result == {3: RAW, 17: RAW, 204: b"From: x\r\nSubject: y\r\n\r\nZ\r\n"}
    assert fake.fetch_calls == ["3,17,204"]


def test_fetch_many_does_not_assume_the_servers_response_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real server is free to answer a UID FETCH in a different order
    than the UID set was given in. fetch_many must map each response back
    to its own uid (read off the response's own UID data item, per RFC
    3501) rather than zipping the response against the request order."""
    fake = MultiFetchIMAP("imap.example.com")
    fake.messages = {3: b"three", 17: b"seventeen", 204: b"two-oh-four"}
    fake.response_order = [204, 3, 17]  # deliberately not request order
    with mailbox(fake) as box:
        result = box.fetch_many([3, 17, 204])
    assert result == {3: b"three", 17: b"seventeen", 204: b"two-oh-four"}


def test_fetch_many_forwards_the_requested_items_to_the_server() -> None:
    """The caller decides what to fetch (full RFC822, or a headers-only
    BODY.PEEK[HEADER.FIELDS (...)]) - fetch_many must send exactly that
    items string on the wire, not hard-code RFC822."""
    fake = MultiFetchIMAP("imap.example.com")
    fake.messages = {3: RAW}
    with mailbox(fake) as box:
        box.fetch_many([3], items="(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    fetch_call = next(
        call for call in fake.calls if call[0] == "uid" and call[1] == "FETCH"
    )
    assert fetch_call[3] == "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])"


def test_fetch_many_chunks_a_large_uid_set() -> None:
    """A server can cap a command line's length; chunking a huge UID set
    into several FETCH calls is the safety net for that, independent of
    the batching win itself."""
    fake = MultiFetchIMAP("imap.example.com")
    uids = list(range(1, FETCH_CHUNK_SIZE * 2 + 5))
    fake.messages = dict.fromkeys(uids, RAW)
    with mailbox(fake) as box:
        result = box.fetch_many(uids)
    assert result == dict.fromkeys(uids, RAW)
    assert len(fake.fetch_calls) == 3
    assert fake.fetch_calls[0].count(",") == FETCH_CHUNK_SIZE - 1
    assert fake.fetch_calls[1].count(",") == FETCH_CHUNK_SIZE - 1
    assert fake.fetch_calls[2].count(",") == 3


def test_fetch_many_raises_when_the_server_reports_failure() -> None:
    with (
        mailbox(FailingIMAP("imap.example.com")) as box,
        pytest.raises(MailError, match="fetch failed"),
    ):
        box.fetch_many([1])


def test_parse_fetch_response_ignores_a_tuple_with_no_uid_data_item() -> None:
    """RFC 3501 guarantees a UID FETCH response always carries a UID data
    item, but the parser must not crash on a malformed line that lacks
    one - it should simply not attribute that payload to any uid, the
    same as a server that never answered for it at all."""
    from newsprint.mail import _parse_fetch_response

    malformed = [(b"1 (RFC822 {5}", b"hello"), b")"]
    assert _parse_fetch_response(malformed) == {}


def test_fetch_many_raises_naming_a_uid_missing_from_the_response() -> None:
    """A response missing one of the requested uids - the server simply
    did not answer for it - must be surfaced, not silently under-counted."""
    fake = MultiFetchIMAP("imap.example.com")
    fake.messages = {3: RAW}  # 17 never answered
    with (
        mailbox(fake) as box,
        pytest.raises(MailError, match=r"no message body.*17"),
    ):
        box.fetch_many([3, 17])


def test_message_count_is_read_from_the_select_response() -> None:
    """The SELECT response already carries the folder's EXISTS count
    (FakeIMAP answers b"2226") - Mailbox must read it off that one round
    trip rather than a second STATUS call."""
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.message_count == 2226


def test_message_count_falls_back_to_zero_for_a_malformed_select_response() -> None:
    class _WeirdCountIMAP(FakeIMAP):
        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            self.selected = (folder, readonly)
            return ("OK", [b"not-a-number"])

    fake = _WeirdCountIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.message_count == 0


def test_trash_folder_is_discovered_from_special_use() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.trash_folder() == "INBOX/Trash"


def test_trash_folder_raises_when_absent() -> None:
    fake = FakeIMAP("imap.example.com")
    fake.list_response = [b'(\\HasNoChildren) "/" "INBOX/toprint"']
    # Anchored on the whole message: this is what the reader is told when
    # a run cannot find anywhere to retire mail to, and "Trash" alone
    # matches almost anything the sentence could decay into.
    with (
        mailbox(fake) as box,
        pytest.raises(
            MailError,
            match=r"^no folder advertises the \\Trash special-use attribute$",
        ),
    ):
        box.trash_folder()


def test_password_for_reads_the_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    """And asks for the entry the README tells the reader to create -
    `keyring set <host> <user>`. Asking under any other name finds
    nothing, and the run stops before it has read a single message."""
    asked: list[tuple[str, str]] = []

    def _get_password(host: str, user: str) -> str:
        asked.append((host, user))
        return "from-keychain"

    monkeypatch.setattr("newsprint.mail.keyring.get_password", _get_password)
    assert password_for("imap.example.com", "someone@example.com") == "from-keychain"
    assert asked == [("imap.example.com", "someone@example.com")]


def test_password_for_explains_how_to_store_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("newsprint.mail.keyring.get_password", lambda host, user: None)
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

    def response(self, code: str):
        return (code, [b"1"])

    def logout(self):
        return ("BYE", [b"bye"])


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


def test_retire_marks_read_and_unstars_before_moving() -> None:
    """Both flag changes must run before MOVE, not after.

    RFC 6851: a successful MOVE expunges the source UID, and RFC 3501
    says a subsequent command against an expunged UID is ignored. STORE
    calls issued after MOVE are therefore dead code - the message lands
    in Trash still starred and unread, silently, because nothing reports
    the STORE as having failed. Read-and-unstar first, then MOVE, is the
    only order under which all three of the user's requirements (read,
    unstarred, moved) can actually take effect.

    This test deliberately replaces the earlier order-asserting test
    (which asserted MOVE, then +Seen, then -Flagged - the dead-code
    order) rather than letting the reorder break it silently.
    """
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        result = box.retire([7], "INBOX/Trash")
    assert result.retired == (7,)
    assert result.failed == ()
    assert result.unrecoverable == ()

    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    assert uid_calls == [
        ("uid", "STORE", "7", "+FLAGS", "(\\Seen)"),
        ("uid", "STORE", "7", "-FLAGS", "(\\Flagged)"),
        ("uid", "MOVE", "7", '"INBOX/Trash"'),
    ]


def test_retire_never_issues_a_command_against_an_expunged_uid() -> None:
    """The general invariant, checked directly against FakeIMAP's expunge
    tracking rather than against one hard-coded call sequence: once a
    MOVE has moved a UID, retire() must never issue another command
    naming that UID. This is exactly the shape of bug that shipped with a
    green suite before FakeIMAP could tell the difference - a command
    issued after the move would silently no-op against a real server, and
    this test is what makes that observable."""
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        box.retire([7], "INBOX/Trash")

    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    move_positions = [i for i, call in enumerate(uid_calls) if call[1] == "MOVE"]
    assert move_positions, "retire() never issued a MOVE"
    moved_uid = uid_calls[move_positions[0]][2]
    after_move = uid_calls[move_positions[0] + 1 :]
    assert not any(call[2] == moved_uid for call in after_move), (
        f"a command was issued against expunged uid {moved_uid!r}: {after_move}"
    )


def test_fake_imap_answers_no_to_a_store_against_an_expunged_uid() -> None:
    """Direct proof that FakeIMAP's expunge tracking actually works - the
    mechanism that makes the test above a meaningful assertion instead of
    a tautology. Before this, FakeIMAP answered OK unconditionally, which
    is exactly why the dead-code STORE-after-MOVE bug passed a green
    suite in the first place."""
    fake = FakeIMAP("imap.example.com")
    fake.uid("MOVE", "7", '"INBOX/Trash"')
    status, _ = fake.uid("STORE", "7", "+FLAGS", "(\\Seen)")
    assert status == "NO"


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
    from newsprint.mail import _quote_mailbox

    assert _quote_mailbox('Weird"Name') == '"Weird\\"Name"'
    assert _quote_mailbox("Back\\Slash") == '"Back\\\\Slash"'


def test_retire_restores_the_star_when_move_fails() -> None:
    """A message whose MOVE fails must remain visibly queued.

    Read-and-unstar now runs before MOVE (STORE-after-MOVE is dead code
    per RFC 6851/3501), so a failed MOVE must re-star the message or it
    would vanish from the star-based queue while still stuck, unmoved, in
    the source folder - the exact bug the Critical finding was raised
    about, from the other direction. \\Seen is deliberately *not* rolled
    back: whether the message was already read before this run cannot be
    known, and it genuinely was printed, so leaving it read matches
    intent."""
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
    assert result.unrecoverable == ()

    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    assert uid_calls == [
        ("uid", "STORE", "7", "+FLAGS", "(\\Seen)"),
        ("uid", "STORE", "7", "-FLAGS", "(\\Flagged)"),
        ("uid", "MOVE", "7", '"INBOX/Trash"'),
        ("uid", "STORE", "7", "+FLAGS", "(\\Flagged)"),
    ]


def test_retire_records_and_warns_distinctly_when_the_rollback_restar_fails() -> None:
    """If MOVE fails and the rescue +FLAGS \\Flagged also fails, the
    message ends up read, unstarred, and stuck in the source folder: gone
    from the star-based queue with nothing visible to the user. That is
    the one state the user cannot discover on their own, so it must be
    recorded distinctly - not just folded into an ordinary failure - so
    the caller can warn about it by name."""
    fake = FakeIMAP("imap.example.com")
    original = fake.uid

    def failing(command, *args):
        is_move = command == "MOVE"
        is_restar = command == "STORE" and args[1:3] == ("+FLAGS", "(\\Flagged)")
        if is_move or is_restar:
            fake.calls.append(("uid", command, *args))
            return ("NO", [b"failure"])
        return original(command, *args)

    fake.uid = failing
    with mailbox(fake) as box:
        result = box.retire([7], "INBOX/Trash")

    assert result.retired == ()
    assert result.failed == (7,)
    assert result.unrecoverable == (7,)


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
    assert result.unrecoverable == ()

    # The failed message ends up re-starred (its rescue rollback), and
    # the other two messages proceed untouched by its failure.
    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    assert ("uid", "STORE", "8", "+FLAGS", "(\\Flagged)") in uid_calls
    assert ("uid", "MOVE", "7", '"INBOX/Trash"') in uid_calls
    assert ("uid", "MOVE", "9", '"INBOX/Trash"') in uid_calls


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


class _DropsOnce:
    """Mixin: the first FETCH raises instead of answering.

    Models what the user's real server did between the picker prompt and
    the fetch of the picked uids: the connection had been sitting idle
    while a human read a scrollable list, and the write that followed
    landed on a socket the server had already closed. imaplib surfaces
    that as BrokenPipeError (or, once the SSL layer notices, IMAP4.abort)
    from inside uid(), not as a NO status - so no amount of checking
    `status != "OK"` can see it.
    """

    def __init__(self, host: str) -> None:
        super().__init__(host)
        self.drop_on_fetch = True
        self.error: BaseException = BrokenPipeError(32, "Broken pipe")

    def uid(self, command: str, *args):
        if command == "FETCH" and self.drop_on_fetch:
            self.drop_on_fetch = False
            raise self.error
        return super().uid(command, *args)


class DroppingIMAP(_DropsOnce, MultiFetchIMAP):
    """Drops its first body fetch, then behaves like MultiFetchIMAP."""


class DroppingSizesIMAP(_DropsOnce, FakeIMAP):
    """Drops its first fetch, then answers RFC822.SIZE like FakeIMAP -
    which MultiFetchIMAP cannot do, since its FETCH override serves
    bodies out of `messages` for every request shape."""


def reconnecting_mailbox(fakes: list[FakeIMAP]) -> Mailbox:
    """A Mailbox whose factory hands out `fakes` in order, so a test can
    tell the connection it reconnected to apart from the one that died."""
    remaining = iter(fakes)
    return Mailbox(
        host="imap.example.com",
        user="someone@example.com",
        password="secret",
        folder="INBOX/toprint",
        imap_factory=lambda host: next(remaining),
    )


def test_fetch_many_reconnects_after_a_dropped_connection() -> None:
    dead, live = DroppingIMAP("h"), MultiFetchIMAP("h")
    for fake in (dead, live):
        fake.messages = {7: RAW}
    with reconnecting_mailbox([dead, live]) as box:
        assert box.fetch_many([7]) == {7: RAW}
    assert ("login", "someone@example.com", "secret") in live.calls
    assert live.selected == ("INBOX/toprint", True)


def test_fetch_sizes_reconnects_after_a_dropped_connection() -> None:
    dead, live = DroppingSizesIMAP("h"), FakeIMAP("h")
    for fake in (dead, live):
        fake.sizes = {7: 4096}
    with reconnecting_mailbox([dead, live]) as box:
        assert box.fetch_sizes([7]) == {7: 4096}


def test_reconnect_survives_an_imap_abort() -> None:
    dead, live = DroppingIMAP("h"), MultiFetchIMAP("h")
    dead.error = imaplib.IMAP4.abort("socket error: [SSL: BAD_LENGTH] bad length")
    for fake in (dead, live):
        fake.messages = {7: RAW}
    with reconnecting_mailbox([dead, live]) as box:
        assert box.fetch_many([7]) == {7: RAW}


def test_reconnect_closes_a_socket_that_cannot_be_closed() -> None:
    """Shutting down the socket that just died usually fails too, and
    that must not stop the reconnect it is clearing the way for."""
    dead, live = DroppingIMAP("h"), MultiFetchIMAP("h")
    dead.shutdown_error = OSError("socket is not connected")
    for fake in (dead, live):
        fake.messages = {7: RAW}
    with reconnecting_mailbox([dead, live]) as box:
        assert box.fetch_many([7]) == {7: RAW}


def test_reconnect_refuses_when_uidvalidity_changed() -> None:
    """A reconnect that lands on a renumbered folder must not fetch.

    UIDVALIDITY changing means every uid this run is holding now names a
    different message - or nothing. Retrying the fetch against the new
    numbering would silently print the wrong newsletters, which is worse
    than the crash this retry exists to prevent.
    """
    dead, live = DroppingIMAP("h"), MultiFetchIMAP("h")
    live.uidvalidity = b"999"
    for fake in (dead, live):
        fake.messages = {7: RAW}
    with (
        reconnecting_mailbox([dead, live]) as box,
        pytest.raises(MailError) as caught,
    ):
        box.fetch_many([7])
    # The whole sentence, including both UIDVALIDITY values: this is the
    # only account the reader gets of why a run stopped, and the two
    # numbers are what tells a renumbering from any other server trouble.
    assert str(caught.value) == (
        "the folder was renumbered while this run was in progress "
        "(UIDVALIDITY b'1' -> b'999'); "
        "the messages this run selected can no longer be identified"
    )


def test_a_second_drop_is_not_retried() -> None:
    """One retry, not a loop - a server that keeps dropping should
    surface as a failed run, not an endless reconnect."""
    dead, also_dead = DroppingIMAP("h"), DroppingIMAP("h")
    with reconnecting_mailbox([dead, also_dead]) as box, pytest.raises(BrokenPipeError):
        box.fetch_many([7])


def test_exit_swallows_a_failed_logout() -> None:
    """A dead socket must not raise from __exit__.

    The user's crash report carried two tracebacks: the real
    BrokenPipeError, and a second one from logout() on the way out, which
    is the one Python reports as the active exception. Closing a
    connection that is already gone is a no-op, not an error.
    """
    fake = FakeIMAP("h")
    fake.logout_error = BrokenPipeError(32, "Broken pipe")
    with mailbox(fake):
        pass


def test_exit_does_not_mask_the_original_error() -> None:
    fake = FakeIMAP("h")
    fake.logout_error = imaplib.IMAP4.abort("socket error")
    with pytest.raises(ValueError, match="the real problem"), mailbox(fake):
        raise ValueError("the real problem")


def test_reconnect_reports_itself_when_a_notifier_is_given() -> None:
    """A silent recovery hides a server that keeps hanging up.

    Root cause for the user's dropped connection is still unknown, so a
    reconnect has to leave a trace in the run's output rather than be
    absorbed into a fetch that merely looks a little slow.
    """
    said: list[str] = []
    dead, live = DroppingIMAP("h"), MultiFetchIMAP("h")
    for fake in (dead, live):
        fake.messages = {7: RAW}
    remaining = iter([dead, live])
    box = Mailbox(
        host="imap.example.com",
        user="someone@example.com",
        password="secret",
        folder="INBOX/toprint",
        imap_factory=lambda host: next(remaining),
        notify=said.append,
    )
    with box:
        box.fetch_many([7])
    assert said == ["The mail server closed the connection; reconnected and retrying."]


def test_nothing_is_reported_when_the_connection_holds() -> None:
    said: list[str] = []
    fake = MultiFetchIMAP("h")
    fake.messages = {7: RAW}
    box = Mailbox(
        host="imap.example.com",
        user="someone@example.com",
        password="secret",
        folder="INBOX/toprint",
        imap_factory=lambda host: fake,
        notify=said.append,
    )
    with box:
        box.fetch_many([7])
    assert said == []


class NoMoveIMAP(FakeIMAP):
    """A server without RFC 6851's MOVE - which is most of IMAP history,
    and still some servers now."""

    def __init__(self, host: str) -> None:
        super().__init__(host)
        self.capabilities = ("IMAP4REV1", "UIDPLUS")


class NoMoveNoUidplusIMAP(FakeIMAP):
    """Neither MOVE nor UIDPLUS: nothing can be expunged by uid, so
    nothing is expunged at all."""

    def __init__(self, host: str) -> None:
        super().__init__(host)
        self.capabilities = ("IMAP4REV1",)


def _uid_commands(fake: FakeIMAP) -> list[str]:
    return [call[1] for call in fake.calls if call[0] == "uid"]


def _uid_calls(fake: FakeIMAP) -> list[tuple[object, ...]]:
    """Every UID command with its arguments.

    _uid_commands above keeps only the verb, which is enough to say what
    order things happened in but says nothing about which message they
    happened to. On the copy-and-delete path that is the whole of what
    matters: a \\Deleted flag or an EXPUNGE naming the wrong uid takes a
    newsletter the reader has not read yet, and no verb-only check would
    see it.
    """
    return [call[1:] for call in fake.calls if call[0] == "uid"]


def test_a_server_without_move_copies_marks_deleted_and_expunges() -> None:
    fake = NoMoveIMAP("h")
    with mailbox(fake) as box:
        result = box.retire([7], "INBOX/Trash")
    assert result.retired == (7,)
    assert _uid_calls(fake) == [
        ("STORE", "7", "+FLAGS", "(\\Seen)"),
        ("STORE", "7", "-FLAGS", "(\\Flagged)"),
        ("COPY", "7", '"INBOX/Trash"'),
        ("STORE", "7", "+FLAGS", "(\\Deleted)"),
        ("EXPUNGE", "7"),
    ]


def test_without_uidplus_the_message_is_flagged_but_never_expunged() -> None:
    """A bare EXPUNGE would remove every message in the folder already
    flagged \\Deleted, including ones another client flagged and has not
    expunged yet. Leaving this one flagged is the honest trade."""
    fake = NoMoveNoUidplusIMAP("h")
    with mailbox(fake) as box:
        result = box.retire([7], "INBOX/Trash")
    assert result.retired == (7,)
    assert _uid_calls(fake) == [
        ("STORE", "7", "+FLAGS", "(\\Seen)"),
        ("STORE", "7", "-FLAGS", "(\\Flagged)"),
        ("COPY", "7", '"INBOX/Trash"'),
        ("STORE", "7", "+FLAGS", "(\\Deleted)"),
    ]


def test_a_server_with_move_still_uses_it() -> None:
    fake = FakeIMAP("h")
    with mailbox(fake) as box:
        box.retire([7], "INBOX/Trash")
    assert "MOVE" in _uid_commands(fake)
    assert "COPY" not in _uid_commands(fake)


class DottedServerIMAP(FakeIMAP):
    """A server whose hierarchy delimiter is "." - Dovecot's default, and
    what the author's own host uses. A config written "INBOX/toprint" is
    simply the wrong name here."""

    def __init__(self, host: str) -> None:
        super().__init__(host)
        self.list_response = [
            b'(\\HasChildren) "." INBOX',
            b'(\\HasNoChildren) "." INBOX.toprint',
            b'(\\HasNoChildren \\Trash) "." INBOX.Trash',
        ]

    def select(self, folder: str, readonly: bool = False):
        self.calls.append(("select", folder, readonly))
        if "/" in folder:
            return ("NO", [b"Mailbox does not exist"])
        self.selected = (folder, readonly)
        return ("OK", [b"2226"])


def test_a_slash_config_opens_on_a_dot_delimited_server() -> None:
    """The delimiter is not standardized, so a config written for one
    server is wrong on the other - and "NO" alone sends people hunting for
    a folder that is right there."""
    fake = DottedServerIMAP("h")
    with mailbox(fake) as box:
        assert box._folder == "INBOX.toprint"
    assert fake.selected == ("INBOX.toprint", True)


def test_a_folder_that_really_is_missing_still_reports_both_attempts() -> None:
    """Rewriting is a guess; when it fails too, say what was tried rather
    than reporting only the name the user did not write."""

    class MissingIMAP(DottedServerIMAP):
        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            return ("NO", [b"Mailbox does not exist"])

    with pytest.raises(MailError, match="also tried"), mailbox(MissingIMAP("h")):
        pass


def test_a_correct_folder_never_triggers_a_list() -> None:
    """The rewrite costs a LIST round trip, so it only happens after a
    SELECT has already failed."""
    fake = FakeIMAP("h")
    with mailbox(fake):
        pass
    assert not any(call[0] == "list" for call in fake.calls)


def test_a_failed_list_gives_up_on_rewriting_the_folder() -> None:
    """No delimiter to be had means no better guess to offer, so the
    original SELECT failure is what the user sees."""

    class ListFailsIMAP(FakeIMAP):
        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            return ("NO", [b"nope"])

        def list(self):
            self.calls.append(("list",))
            return ("NO", [b"failure"])

    with (
        pytest.raises(MailError, match="could not open folder"),
        mailbox(ListFailsIMAP("h")),
    ):
        pass


def test_list_lines_that_are_not_bytes_are_skipped() -> None:
    """imaplib widens a response element to cover FETCH's (info, payload)
    tuples; LIST never produces one, but the type says it might."""

    class OddListIMAP(FakeIMAP):
        def __init__(self, host: str) -> None:
            super().__init__(host)
            self.list_response = [
                (b"unexpected", b"tuple"),
                b"no delimiter here at all",
                b'(\\HasNoChildren) "." INBOX.toprint',
            ]

        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            if "/" in folder:
                return ("NO", [b"nope"])
            self.selected = (folder, readonly)
            return ("OK", [b"1"])

    with mailbox(OddListIMAP("h")) as box:
        assert box._folder == "INBOX.toprint"


def test_a_delimiter_that_changes_nothing_is_not_retried() -> None:
    """A folder with no separator in it cannot be rewritten, so there is
    no second attempt to report."""

    class FlatIMAP(DottedServerIMAP):
        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            return ("NO", [b"nope"])

    box = Mailbox(
        host="h",
        user="u",
        password="p",
        folder="toprint",
        imap_factory=lambda host: FlatIMAP(host),
    )
    with pytest.raises(MailError) as caught, box:
        pass
    # The whole message: "not in" says nothing about what the sentence
    # does end with, and this one is what the reader gets when the folder
    # they named cannot be opened.
    assert str(caught.value) == "could not open folder 'toprint': NO"


@pytest.mark.parametrize("failing", ["COPY", "STORE_DELETED"])
def test_a_failure_mid_fallback_is_reported_and_the_star_restored(
    failing: str,
) -> None:
    """Each step of the pre-MOVE sequence can fail on its own, and the
    message must be re-starred either way rather than vanishing from a
    star-based queue."""

    class FailingIMAP(NoMoveIMAP):
        def uid(self, command: str, *args):
            if command == "COPY" and failing == "COPY":
                return ("NO", [b"refused"])
            if (
                command == "STORE"
                and failing == "STORE_DELETED"
                and args
                and "Deleted" in str(args[-1])
            ):
                return ("NO", [b"refused"])
            return super().uid(command, *args)

    fake = FailingIMAP("h")
    with mailbox(fake) as box:
        result = box.retire([7], "INBOX/Trash")
    assert result.retired == ()
    assert result.failed == (7,)
    assert result.unrecoverable == ()


def test_a_list_with_no_delimiter_anywhere_gives_up() -> None:
    """A LIST whose every line is unparseable yields no delimiter, and the
    loop has to fall off the end rather than guess one."""

    class NoDelimiterIMAP(FakeIMAP):
        def __init__(self, host: str) -> None:
            super().__init__(host)
            self.list_response = [b"garbage", b"more garbage"]

        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            return ("NO", [b"nope"])

    with (
        pytest.raises(MailError, match="could not open folder"),
        mailbox(NoDelimiterIMAP("h")),
    ):
        pass


class TrashIMAP(FakeIMAP):
    """A server holding retired messages in the trash, searchable by
    Message-ID the way a real one is."""

    def __init__(self, host: str) -> None:
        super().__init__(host)
        self.in_trash = {"<a@x>": 91, "<b@x>": 92}
        self.moved: list[tuple[str, str]] = []
        self.flagged: list[str] = []

    def uid(self, command: str, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            criteria = " ".join(str(a) for a in args if a is not None)
            for message_id, uid in self.in_trash.items():
                if f'"{message_id}"' in criteria:
                    return ("OK", [str(uid).encode()])
            return ("OK", [b""])
        if command == "STORE" and len(args) > 2 and "Flagged" in str(args[2]):
            self.flagged.append(str(args[0]))
            return ("OK", [b""])
        if command == "MOVE":
            self.moved.append((str(args[0]), str(args[1])))
            return ("OK", [b""])
        return ("OK", [b""])


def test_unretire_restars_before_moving_back() -> None:
    """Flags travel with a message, so starring it while it is still in
    the trash avoids needing its new uid afterwards - which would take
    UIDPLUS's COPYUID to learn."""
    fake = TrashIMAP("h")
    with mailbox(fake) as box:
        result = box.unretire(["<a@x>", "<b@x>"], "INBOX/Trash")
    assert result.restored == ("<a@x>", "<b@x>")
    assert result.missing == () and result.failed == ()
    assert fake.flagged == ["91", "92"]
    # The target is quoted, as any mailbox name handed to MOVE must be.
    assert fake.moved == [("91", '"INBOX/toprint"'), ("92", '"INBOX/toprint"')]
    # Every argument, not just the verbs: a STORE that names the wrong
    # flag item, or the wrong uid, puts the star on the wrong message or
    # on nothing at all, and a verb-only check sees neither.
    assert _uid_calls(fake) == [
        ("SEARCH", None, 'HEADER Message-ID "<a@x>"'),
        ("STORE", "91", "+FLAGS", "(\\Flagged)"),
        ("MOVE", "91", '"INBOX/toprint"'),
        ("SEARCH", None, 'HEADER Message-ID "<b@x>"'),
        ("STORE", "92", "+FLAGS", "(\\Flagged)"),
        ("MOVE", "92", '"INBOX/toprint"'),
    ]
    # And the trash has to be opened writable, or none of it can happen.
    assert ("select", '"INBOX/Trash"', False) in fake.calls


def test_unretire_reports_messages_the_trash_no_longer_holds() -> None:
    """Bounded by the trash: once the host empties it there is nothing to
    restore, and the run log cannot help."""
    fake = TrashIMAP("h")
    with mailbox(fake) as box:
        # The missing one first: a message the trash no longer holds is a
        # reason to go on to the next, never to abandon the rest.
        result = box.unretire(["<gone@x>", "<a@x>"], "INBOX/Trash")
    assert result.restored == ("<a@x>",)
    assert result.missing == ("<gone@x>",)


def test_unretire_reports_a_move_that_fails() -> None:
    class StuckIMAP(TrashIMAP):
        def uid(self, command: str, *args):
            if command == "MOVE":
                self.calls.append(("uid", command, *args))
                return ("NO", [b"refused"])
            return super().uid(command, *args)

    fake = StuckIMAP("h")
    with mailbox(fake) as box:
        # And a move that fails is likewise no reason to stop: the second
        # message here is refused too, so both must be reported.
        result = box.unretire(["<a@x>", "<b@x>"], "INBOX/Trash")
    assert result.failed == ("<a@x>", "<b@x>") and result.restored == ()


def test_unretire_with_nothing_to_do_touches_no_mail() -> None:
    fake = TrashIMAP("h")
    with mailbox(fake) as box:
        assert box.unretire([], "INBOX/Trash") == UnretireResult()
    assert not any(call[0] == "uid" for call in fake.calls)


def test_unretire_needs_the_trash_folder_writable() -> None:
    class ReadOnlyTrashIMAP(TrashIMAP):
        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            if not readonly:
                return ("NO", [b"read-only"])
            self.selected = (folder, readonly)
            return ("OK", [b"1"])

    with (
        pytest.raises(MailError, match="writable"),
        mailbox(ReadOnlyTrashIMAP("h")) as box,
    ):
        box.unretire(["<a@x>"], "INBOX/Trash")


def test_folders_lists_only_what_can_be_opened() -> None:
    """\\Noselect marks a container that exists only to hold others -
    Gmail's "[Gmail]" is the common one - and offering it would hand the
    user a name that cannot be selected."""
    fake = FakeIMAP("h")
    # Each thing that must be skipped has a real folder behind it: a line
    # the listing cannot use is a reason to look at the next one, never to
    # stop and hand back a short list as though it were the whole mailbox.
    fake.list_response = [
        (b"unexpected", b"tuple"),
        b'(\\HasChildren) "." INBOX',
        b"not a list line at all",
        b'(\\Noselect \\HasChildren) "/" "[Gmail]"',
        b'(\\HasNoChildren) "." "INBOX.Beverly and Ed"',
    ]
    with mailbox(fake) as box:
        assert box.folders() == ["INBOX", "INBOX.Beverly and Ed"]


def test_folders_raises_when_the_server_refuses_to_list() -> None:
    with (
        pytest.raises(MailError, match="LIST failed"),
        mailbox(FailingIMAP("h")) as box,
    ):
        box.folders()


def test_unretire_treats_an_empty_search_answer_as_missing() -> None:
    """Servers differ in how they say "nothing matched": some answer OK
    with a single empty line, some with no lines at all, and a server
    under strain can answer NO outright. None of the three is a uid, and
    reading one as though it were would restar and move whatever came
    back first.
    """

    class TerseIMAP(TrashIMAP):
        """OK, and no data lines whatsoever."""

        def uid(self, command: str, *args):
            if command == "SEARCH":
                self.calls.append(("uid", command, *args))
                return ("OK", [])
            return super().uid(command, *args)

    class RefusingIMAP(TrashIMAP):
        """NO, with a uid in the data - which must still not be used."""

        def uid(self, command: str, *args):
            if command == "SEARCH":
                self.calls.append(("uid", command, *args))
                return ("NO", [b"91"])
            return super().uid(command, *args)

    for server in (TerseIMAP("h"), RefusingIMAP("h")):
        with mailbox(server) as box:
            result = box.unretire(["<a@x>"], "INBOX/Trash")
        assert result.missing == ("<a@x>",), f"{type(server).__name__} misread"
        assert result.restored == ()
        assert server.flagged == [], "nothing may be starred on a failed search"
        assert server.moved == []


def test_the_connection_is_opened_against_the_configured_host() -> None:
    """The host is the one thing a Mailbox cannot work out for itself,
    and connecting to the wrong one - or to nothing - fails in a way that
    looks like every other network problem."""
    asked: list[str] = []
    fake = FakeIMAP("imap.example.com")

    def _factory(host: str) -> FakeIMAP:
        asked.append(host)
        return fake

    box = Mailbox(
        host="imap.example.com",
        user="someone@example.com",
        password="secret",
        folder="INBOX/toprint",
        imap_factory=_factory,
    )
    with box:
        pass
    assert asked == ["imap.example.com"]


def test_a_new_mailbox_has_counted_nothing_yet() -> None:
    """message_count is filled in by opening the folder. Before that it
    has to read as none rather than as one, or a caller checking it would
    act on a message that does not exist."""
    box = Mailbox(
        host="h",
        user="u",
        password="p",
        folder="INBOX/toprint",
        imap_factory=lambda host: FakeIMAP(host),
    )
    assert box.message_count == 0


def test_the_uidvalidity_read_at_open_is_the_folders_own() -> None:
    """A reconnect compares this against what the folder says afterwards,
    so reading the wrong response code - or none - would leave every
    reconnect either always refusing or never noticing a renumbering."""
    fake = FakeIMAP("imap.example.com")
    fake.uidvalidity = b"4242"
    with mailbox(fake) as box:
        assert box._uidvalidity == b"4242"
    assert ("response", "UIDVALIDITY") in fake.calls


def test_a_fetch_response_entry_without_a_payload_is_skipped() -> None:
    """A UID FETCH response is a mixture: an (info, payload) pair per
    message, and bare lines - the closing b")" after each, and whatever
    untagged chatter the server adds. Only a pair carries a message, and
    only a pair of two: a one-element tuple has no payload to take, and
    reading it as though it had would raise mid-run."""
    from newsprint.mail import _parse_fetch_response

    parsed = _parse_fetch_response(
        [
            b")",
            (b"1 (UID 7 RFC822 {3}",),  # a pair-shaped entry with nothing in it
            None,
            (b"2 (UID 8 RFC822 {3}", b"raw"),
        ]
    )
    assert parsed == {8: b"raw"}


def test_a_folder_that_reports_no_count_still_opens() -> None:
    """The number after SELECT is cosmetic - it feeds the picker's "N
    messages" line and nothing else - so a server that answers without a
    parseable count opens the folder anyway, reporting none rather than
    inventing one."""

    class CountlessIMAP(FakeIMAP):
        def select(self, folder: str, readonly: bool = False):
            self.calls.append(("select", folder, readonly))
            self.selected = (folder, readonly)
            return ("OK", [b""])

    fake = CountlessIMAP("h")
    with mailbox(fake) as box:
        assert box.message_count == 0


def test_a_mailbox_used_after_it_closes_says_so() -> None:
    """Leaving the block logs out and forgets the connection. Reaching for
    it afterwards has to say that plainly, rather than fail somewhere
    deeper on whatever was left behind."""
    fake = FakeIMAP("h")
    box = mailbox(fake)
    with box:
        pass
    with pytest.raises(MailError, match=r"^mailbox is not open; use it as a "):
        box.folders()


def test_fetch_many_asks_for_the_uid_and_the_whole_message_by_default() -> None:
    """Both halves matter: the raw message is what gets printed, and the
    UID is how each response is matched back to the message that asked
    for it - a batched fetch returns them in whatever order it likes."""
    fake = MultiFetchIMAP("h")
    fake.messages = {7: RAW}
    with mailbox(fake) as box:
        assert box.fetch_many([7]) == {7: RAW}
    fetches = [call for call in fake.calls if call[:2] == ("uid", "FETCH")]
    assert fetches == [("uid", "FETCH", "7", "(UID RFC822)")]


def test_a_folder_name_that_is_not_utf8_is_still_listed() -> None:
    """IMAP folder names are meant to be modified UTF-7, and servers send
    raw bytes anyway. A name that will not decode must not take the whole
    listing down with it - the user has other folders to choose from."""
    fake = FakeIMAP("h")
    fake.list_response = [
        b'(\\HasNoChildren) "." "INBOX.caf\xe9"',
        b'(\\HasNoChildren) "." "INBOX.toprint"',
    ]
    with mailbox(fake) as box:
        names = box.folders()
    assert names[1] == "INBOX.toprint"
    assert names[0].startswith("INBOX.caf")
