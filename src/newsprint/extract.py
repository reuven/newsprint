"""Turn a MIME message into a Document.

The HTML that comes out is the message's own, untouched. Cleaning is a
separate concern and lives in clean.py.
"""

import email
import email.utils
import re
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.message import Message
from html import escape

from .config import PublicationNames
from .models import Document, Origin

_EMPTY_NAMES = PublicationNames(by_address={}, by_list_id={})


def _decode_words(text: str) -> str:
    """Decode any RFC 2047 encoded words in `text`.

    Used both for header values straight off the message (via _header
    below) and for publication names pulled from publications.toml: a
    hand-edited or seeded override can itself still be a raw encoded word
    (e.g. copied verbatim from a From header), and it should print as
    text either way.
    """
    return str(make_header(decode_header(text)))


def _header(message: Message, name: str, default: str = "") -> str:
    raw = message.get(name)
    if raw is None:
        return default
    return _decode_words(raw)


def _decode(part: Message) -> str:
    payload = part.get_payload(decode=True)
    # get_payload is typed as the union of all its overloads' results;
    # with decode=True it is bytes, or None for a container part. One
    # isinstance narrows both cases at once.
    if not isinstance(payload, bytes):
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


def _publication(
    message: Message, address: str, display: str, names: PublicationNames
) -> str:
    """Resolution order: List-Id override -> List-Id -> address override
    -> From display name -> domain.

    List-Id identifies the newsletter; the address only identifies the
    sending system. One address can send many distinct newsletters - the
    New York Times sends The Morning, Cooking, DealBook, Five Weeknight
    Dishes, The Veggie, and named columnists like David French and
    Jamelle Bouie all from nytdirect@nytimes.com - so an address-keyed
    override is only consulted once a message has no List-Id at all to
    identify a specific newsletter with. A List-Id-keyed override still
    lets a user rename one specific newsletter.
    """
    list_id = _header(message, "List-Id")
    if list_id:
        label = list_id.split("<")[0].strip().strip('"').strip()
        if label:
            override = names.by_list_id.get(label.lower())
            if override:
                return _decode_words(override)
            return label

    address_override = names.by_address.get(address)
    if address_override:
        return _decode_words(address_override)

    if display:
        return display

    return address.partition("@")[2] or "unknown"


# A label that is a mailing-system identifier rather than a name:
# Mailchimp hands out hosts like
# "b98e2de85f03865f1d38de74f.77913.list-id.mcsv.net", which identify the
# list to Mailchimp and nobody else. Falling back to the sender's own
# domain is strictly more informative (that message is Benedict Evans, and
# ben-evans.com says so). 16 hex characters is far past what any readable
# publication name would be; the shortest real one in the archive is 32.
_OPAQUE_HOST_LABEL = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)


def _is_opaque_host(host: str) -> bool:
    """True when `host` is a machine identifier rather than a name.

    A dedicated `host.endswith("mcsv.net")` branch used to sit in front of
    this. Mutation testing showed it was dead: breaking that string
    entirely changed no behavior, because every Mailchimp host in the
    archive - all three of them - leads with a 32-character hex id the
    label rule already catches. The general rule is the whole rule.
    """
    return any(_OPAQUE_HOST_LABEL.match(label) for label in host.split("."))


def _source_host(message: Message, address: str) -> str | None:
    """The host identifying where this newsletter comes from.

    Prefers the List-Id host over the sender's domain because it names
    the newsletter rather than the sending system: one address sends many
    newsletters (nytdirect@nytimes.com sends The Morning, DealBook, and
    named columnists alike), and on the big platforms the List-Id host is
    the only place the publication's own name appears at all -
    "cloudirregular.substack.com" for a message whose From is just
    "Forrest Brazeal".
    """
    list_id = _header(message, "List-Id")
    if "<" in list_id:
        host = list_id.rpartition("<")[2].rstrip(">").strip().lower()
        if host and not _is_opaque_host(host):
            return host
    return address.partition("@")[2].lower() or None


def _date(message: Message) -> datetime:
    raw = message.get("Date")
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
    return datetime.now(UTC)


def extract(
    raw: bytes,
    uid: int | None = None,
    names: PublicationNames | None = None,
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
        publication=_publication(message, address, display, names or _EMPTY_NAMES),
        title=_header(message, "Subject", default="(no subject)"),
        author=display or None,
        source_host=_source_host(message, address),
        date=_date(message),
        html=_body_html(message),
    )
