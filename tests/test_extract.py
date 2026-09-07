from datetime import UTC, datetime
from pathlib import Path

import pytest

from shabbat_print.extract import extract

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


def test_names_override_wins() -> None:
    names = {"noreply@news.bloomberg.com": "Money Stuff (Bloomberg)"}
    assert extract(HTML_MESSAGE, names=names).publication == "Money Stuff (Bloomberg)"


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

    from shabbat_print.extract import _decode

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
