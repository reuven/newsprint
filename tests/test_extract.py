from datetime import UTC, datetime
from email.charset import QP, Charset
from pathlib import Path

import pytest

from newsprint.config import PublicationNames
from newsprint.extract import extract

FIXTURES = Path(__file__).parent / "fixtures"


def message(headers: str, body: str) -> bytes:
    return (headers.strip() + "\n\n" + body).encode()


HTML_MESSAGE = message(
    """
From: Matt Levine <noreply@news.bloomberg.com>
Subject: Money Stuff: Private Credit Gets Complicated
Date: Fri, 5 Sep 2026 14:22:55 +0000
Message-ID: <abc123@bloomberg.net>
List-Id: Money Stuff <moneystuff.bloomberg.com>
Content-Type: text/html; charset="utf-8"
""",
    "<html><body><p>Private credit is having a moment.</p></body></html>",
)


def test_reads_the_obvious_fields() -> None:
    document = extract(HTML_MESSAGE, uid=42)
    assert document.title == "Money Stuff: Private Credit Gets Complicated"
    assert document.origin.identifier == "<abc123@bloomberg.net>"
    assert document.origin.uid == 42
    assert document.origin.kind == "email"
    assert "Private credit is having a moment." in document.html


def test_date_is_timezone_aware() -> None:
    document = extract(HTML_MESSAGE)
    assert document.date == datetime(2026, 9, 5, 14, 22, 55, tzinfo=UTC)


def test_publication_comes_from_list_id() -> None:
    assert extract(HTML_MESSAGE).publication == "Money Stuff"


def test_publication_falls_back_to_display_name() -> None:
    raw = message(
        """
From: The Economist <noreply@e.economist.com>
Subject: Espresso
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Good morning.</p></body></html>",
    )
    assert extract(raw).publication == "The Economist"


def test_publication_falls_back_to_domain() -> None:
    raw = message(
        """
From: noreply@dailydoseofds.com
Subject: Today's dose
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hello.</p></body></html>",
    )
    assert extract(raw).publication == "dailydoseofds.com"


def test_address_override_wins_when_no_list_id() -> None:
    """A user can still rename a sender's newsletter by address - but only
    when the message carries no List-Id to identify a specific newsletter
    from that sender."""
    raw = message(
        """
From: The Economist <noreply@e.economist.com>
Subject: Espresso
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Good morning.</p></body></html>",
    )
    names = PublicationNames(
        by_address={"noreply@e.economist.com": "The Economist (Espresso)"},
        by_list_id={},
    )
    assert extract(raw, names=names).publication == "The Economist (Espresso)"


def test_list_id_wins_over_address_override() -> None:
    """One address can send many distinct newsletters (this is exactly the
    NYT's nytdirect@nytimes.com case): List-Id identifies the newsletter,
    the address only identifies the sending system, so an address-keyed
    override must not blur every newsletter from that address into one
    name."""
    names = PublicationNames(
        by_address={"noreply@news.bloomberg.com": "Jamelle Bouie"},
        by_list_id={},
    )
    assert extract(HTML_MESSAGE, names=names).publication == "Money Stuff"


def test_list_id_override_wins_over_list_id_itself() -> None:
    """A user can still rename one specific newsletter, keyed by List-Id."""
    names = PublicationNames(
        by_address={},
        by_list_id={"money stuff": "Matt Levine's Money Stuff"},
    )
    assert extract(HTML_MESSAGE, names=names).publication == "Matt Levine's Money Stuff"


def test_list_id_override_beats_address_override_too() -> None:
    names = PublicationNames(
        by_address={"noreply@news.bloomberg.com": "Bloomberg (address override)"},
        by_list_id={"money stuff": "Matt Levine's Money Stuff"},
    )
    assert extract(HTML_MESSAGE, names=names).publication == "Matt Levine's Money Stuff"


def test_address_override_is_mime_decoded() -> None:
    """publications.toml is hand-edited and occasionally seeded from raw
    headers; an override value that is still a raw RFC 2047 encoded word
    must print as text, not as gibberish."""
    raw = message(
        """
From: Someone <simonw@substack.com>
Subject: A post
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    encoded = "=?utf-8?b?U2ltb24gV2lsbGlzb24=?="
    names = PublicationNames(by_address={"simonw@substack.com": encoded}, by_list_id={})
    assert extract(raw, names=names).publication == "Simon Willison"


def test_list_id_override_is_mime_decoded() -> None:
    encoded = "=?utf-8?b?RGF2aWQgRnJlbmNo?="
    names = PublicationNames(by_address={}, by_list_id={"money stuff": encoded})
    assert extract(HTML_MESSAGE, names=names).publication == "David French"


def test_base64_encoded_list_id_is_decoded() -> None:
    """A real base64 (RFC 2047 'b') encoded word in the List-Id header
    itself, as some senders occasionally use."""
    raw = message(
        """
From: David French <df.nytimes.com@nytimes.com>
Subject: A column
Date: Sat, 6 Sep 2026 06:00:00 +0000
List-Id: =?utf-8?b?RGF2aWQgRnJlbmNo?= <df.nytimes.com>
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    assert extract(raw).publication == "David French"


def test_quoted_printable_encoded_list_id_is_decoded() -> None:
    """A real quoted-printable (RFC 2047 'q') encoded word in the List-Id
    header, generated the way a mail server actually would."""
    charset = Charset("utf-8")
    charset.header_encoding = QP
    encoded_label = charset.header_encode("David French — NYT")
    raw = message(
        f"""
From: David French <df.nytimes.com@nytimes.com>
Subject: A column
Date: Sat, 6 Sep 2026 06:00:00 +0000
List-Id: {encoded_label} <df.nytimes.com>
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    assert extract(raw).publication == "David French — NYT"


def test_encoded_subject_is_decoded() -> None:
    raw = message(
        """
From: Simon Willison <simon@example.com>
Subject: =?utf-8?B?U2ltb24ncyBOZXdzbGV0dGVy?=
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    assert extract(raw).title == "Simon's Newsletter"


def test_plain_text_only_message_is_wrapped() -> None:
    raw = message(
        """
From: Someone <someone@example.com>
Subject: Plain
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/plain; charset="utf-8"
""",
        "Just words.\nAnd <angle> brackets.",
    )
    html = extract(raw).html
    assert "<pre>" in html
    assert "&lt;angle&gt;" in html


def test_the_largest_html_part_wins() -> None:
    raw = (
        b"From: Someone <someone@example.com>\n"
        b"Subject: Multipart\n"
        b"Date: Sat, 6 Sep 2026 06:00:00 +0000\n"
        b'Content-Type: multipart/alternative; boundary="B"\n'
        b"\n"
        b"--B\n"
        b'Content-Type: text/html; charset="utf-8"\n'
        b"\n"
        b"<p>short</p>\n"
        b"--B\n"
        b'Content-Type: text/html; charset="utf-8"\n'
        b"\n"
        b"<p>this alternative is considerably longer than the other one</p>\n"
        b"--B--\n"
    )
    assert "considerably longer" in extract(raw).html


def test_missing_date_header_uses_current_time() -> None:
    raw = message(
        """
From: Someone <someone@example.com>
Subject: No date
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    doc = extract(raw)
    assert doc.date.tzinfo is not None
    assert doc.date.tzinfo == UTC


def test_malformed_date_header_uses_current_time() -> None:
    raw = message(
        """
From: Someone <someone@example.com>
Subject: Bad date
Date: not-a-valid-date
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    doc = extract(raw)
    assert doc.date.tzinfo is not None
    assert doc.date.tzinfo == UTC


def test_naive_date_gets_utc_timezone() -> None:
    raw = message(
        """
From: Someone <someone@example.com>
Subject: Naive date
Date: Sat, 6 Sep 2026 06:00:00
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    doc = extract(raw)
    assert doc.date == datetime(2026, 9, 6, 6, 0, 0, tzinfo=UTC)


def test_message_with_attachment_skips_it() -> None:
    raw = (
        b"From: Someone <someone@example.com>\n"
        b"Subject: With attachment\n"
        b"Date: Sat, 6 Sep 2026 06:00:00 +0000\n"
        b'Content-Type: multipart/mixed; boundary="B"\n'
        b"\n"
        b"--B\n"
        b'Content-Type: text/html; charset="utf-8"\n'
        b"\n"
        b"<p>This is the content</p>\n"
        b"--B\n"
        b"Content-Type: application/pdf\n"
        b'Content-Disposition: attachment; filename="document.pdf"\n'
        b"\n"
        b"PDF content here\n"
        b"--B--\n"
    )
    html = extract(raw).html
    assert "This is the content" in html
    assert "PDF content" not in html


def test_empty_message_body_returns_empty_html() -> None:
    raw = message(
        """
From: Someone <someone@example.com>
Subject: Empty
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "",
    )
    html = extract(raw).html
    assert html == ""


def test_missing_subject_uses_default() -> None:
    raw = message(
        """
From: Someone <someone@example.com>
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    assert extract(raw).title == "(no subject)"


def test_missing_from_uses_default_publication() -> None:
    raw = message(
        """
Subject: Headerless
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    assert extract(raw).publication == "unknown"


def test_message_with_only_non_text_parts() -> None:
    raw = (
        b"From: Someone <someone@example.com>\n"
        b"Subject: Only images\n"
        b"Date: Sat, 6 Sep 2026 06:00:00 +0000\n"
        b'Content-Type: multipart/mixed; boundary="B"\n'
        b"\n"
        b"--B\n"
        b"Content-Type: image/png\n"
        b"\n"
        b"PNG data\n"
        b"--B--\n"
    )
    html = extract(raw).html
    assert html == "<html><body></body></html>"


def test_unsupported_charset_falls_back_to_utf8() -> None:
    raw = (
        b"From: Someone <someone@example.com>\n"
        b"Subject: Bad charset\n"
        b"Date: Sat, 6 Sep 2026 06:00:00 +0000\n"
        b'Content-Type: text/plain; charset="nonexistent-encoding"\n'
        b"\n"
        b"Plain text message"
    )
    html = extract(raw).html
    assert "Plain text message" in html
    assert "<pre>" in html


def test_decode_with_none_payload_returns_empty_string() -> None:
    """_decode() returns empty string when part.get_payload(decode=True) is None.

    This occurs when a Message has a sub-message as payload (multipart container
    at the part level), which causes get_payload(decode=True) to return None.
    While extract() filters multipart containers, _decode() must handle this
    defensively for robustness.
    """
    from email.message import Message

    from newsprint.extract import _decode

    # Create a part with a sub-message as payload (simulating a multipart at
    # the part level), which makes get_payload(decode=True) return None
    part = Message()
    part["Content-Type"] = "text/plain"
    sub_message = Message()
    sub_message.set_payload("nested content")
    part.set_payload([sub_message])

    assert _decode(part) == ""


@pytest.mark.skipif(not FIXTURES.exists(), reason="run `make fixtures` first")
def test_every_fixture_extracts() -> None:
    """Real mail, every sender. Nothing may raise, and nothing may come back
    without a publication and a title."""
    paths = sorted(FIXTURES.glob("*.eml"))
    assert paths, "no fixtures; run `make fixtures`"
    for path in paths:
        document = extract(path.read_bytes())
        assert document.publication, path.name
        assert document.html, path.name


def test_a_mailchimp_list_id_falls_back_to_the_senders_domain() -> None:
    """Mailchimp's List-Id host identifies the list to Mailchimp and to
    nobody else - "e5101197dd74666a141a34e7b.160398.list-id.mcsv.net"
    names no publication a reader would recognize. Trusting it would put
    "DAN OSHINSKY (mcsv.net)" in the picker instead of the sender's own
    inboxcollective.com.
    """
    raw = message(
        """
From: Dan Oshinsky <dan@inboxcollective.com>
Subject: Welcome to 5 Days to Fix Your Newsletter!
Date: Mon, 8 Sep 2026 09:00:00 +0000
Message-ID: <x@inboxcollective.com>
List-Id: Inbox Collective <e5101197dd74666a141a34e7b.160398.list-id.mcsv.net>
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hello.</p></body></html>",
    )
    assert extract(raw).source_host == "inboxcollective.com"


def test_an_opaque_hex_list_id_host_falls_back_even_off_mailchimp() -> None:
    """The hex-label test is the general rule the mcsv.net check is only
    one instance of: a label of 16+ hex characters is a machine
    identifier, whatever domain it sits under. Splitting on anything but
    "." never sees those labels at all.
    """
    raw = message(
        """
From: Benedict Evans <benedict@ben-evans.com>
Subject: The new gatekeepers
Date: Mon, 8 Sep 2026 09:00:00 +0000
Message-ID: <y@ben-evans.com>
List-Id: Benedict Evans <b98e2de85f03865f1d38de74f.77913.list-id.example.net>
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hello.</p></body></html>",
    )
    assert extract(raw).source_host == "ben-evans.com"


def test_a_readable_list_id_host_is_kept() -> None:
    """The fallback must not swallow the useful case: on the big
    platforms the List-Id host is the only place the publication's own
    name appears."""
    raw = message(
        """
From: Forrest Brazeal <forrest@substack.com>
Subject: Cloud Irregular
Date: Mon, 8 Sep 2026 09:00:00 +0000
Message-ID: <z@substack.com>
List-Id: Cloud Irregular <cloudirregular.substack.com>
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hello.</p></body></html>",
    )
    assert extract(raw).source_host == "cloudirregular.substack.com"
