import imaplib
from datetime import date

import pytest

from shabbat_print.mail import FETCH_CHUNK_SIZE, Mailbox, MailError, password_for

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

    def logout(self):
        self.calls.append(("logout",))
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
    from shabbat_print.mail import _parse_fetch_response

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
    from shabbat_print.mail import _quote_mailbox

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
