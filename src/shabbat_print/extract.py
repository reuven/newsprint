"""Turn a MIME message into a Document.

The HTML that comes out is the message's own, untouched. Cleaning is a
separate concern and lives in clean.py.
"""

import email
import email.utils
from collections.abc import Mapping
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from html import escape

from .models import Document, Origin


def _header(message: Message, name: str, default: str = "") -> str:
    raw = message.get(name)
    if raw is None:
        return default
    return str(make_header(decode_header(raw)))


def _decode(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _body_html(message: Message) -> str:
    """Prefer the largest text/html part; fall back to text/plain."""
    html_parts: list[str] = []
    text_parts: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        content_type = part.get_content_type()
        if content_type == "text/html":
            html_parts.append(_decode(part))
        elif content_type == "text/plain":
            text_parts.append(_decode(part))

    if html_parts:
        return max(html_parts, key=len)
    if text_parts:
        return (
            f"<html><body><pre>{escape(max(text_parts, key=len))}</pre></body></html>"
        )
    return "<html><body></body></html>"


def _publication(message: Message, address: str, names: Mapping[str, str]) -> str:
    if address in names:
        return names[address]

    list_id = _header(message, "List-Id")
    if list_id:
        label = list_id.split("<")[0].strip().strip('"').strip()
        if label:
            return label

    display, _ = email.utils.parseaddr(_header(message, "From"))
    if display:
        return display

    return address.partition("@")[2] or "unknown"


def _date(message: Message) -> datetime:
    raw = message.get("Date")
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except TypeError, ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
    return datetime.now(UTC)


def extract(
    raw: bytes,
    uid: int | None = None,
    names: Mapping[str, str] | None = None,
) -> Document:
    message = email.message_from_bytes(raw)
    display, raw_address = email.utils.parseaddr(_header(message, "From"))
    address = raw_address.lower()

    return Document(
        origin=Origin(
            kind="email",
            identifier=_header(message, "Message-ID", default=address),
            uid=uid,
        ),
        publication=_publication(message, address, names or {}),
        title=_header(message, "Subject", default="(no subject)"),
        author=display or None,
        date=_date(message),
        html=_body_html(message),
    )
