"""Turn a fetched web page into a Document (phase8-urls.md).

trafilatura reads the page's own rendered DOM, never its CSS or
JavaScript - which is exactly why a soft paywall (the server sends the
full article and hides it with CSS) yields the full text with no special
handling: browser.fetch_html() already hands this module the DOM as
Playwright rendered it, CSS included but irrelevant to trafilatura's own
extraction.

Everything downstream of the Document this produces - clean.py, render.py,
trim.py, contents.py, stamp.py - was built to handle Origin(kind="url",
...) from the start and needs no change (see models.Origin's own
docstring): this module's only job is building one correctly.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from urllib.parse import urlparse

import trafilatura

from .models import Document, Origin


class ExtractionError(Exception):
    """trafilatura found no usable article text at this URL."""


@dataclass(frozen=True, slots=True)
class Extracted:
    """The built Document, plus the raw word count it was judged by.

    word_count is measured on trafilatura's own extracted text, before
    clean.py ever sees it - the earliest point a hard paywall's teaser
    (a headline and a "subscribe to continue" paragraph) can be told apart
    from a real article, which is what the caller's own thin-content
    prompt (phase8-urls.md's first risk) is built on.
    """

    document: Document
    word_count: int


def _publication(url: str, sitename: str | None) -> str:
    if sitename:
        return sitename
    return urlparse(url).hostname or url


def _parsed_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def _body_html(text: str, title: str) -> str:
    """Wrap each extracted line in its own <p>, the same fallback shape
    extract.py uses for a plain-text email body (see its _body_html).

    trafilatura's own text output puts the article's headline on its own
    first line, verbatim - render.py already synthesises its own
    <h1>document.title</h1> from Document.title, so keeping it here too
    would print the same headline twice, the same duplication clean.py's
    _strip_duplicate_title_block exists to remove from an email body. It
    is simpler to just never emit it here: drop the first line only when
    it is (after trimming) exactly the title, leaving everything else -
    including a genuine byline or dateline immediately below it -
    untouched.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and lines[0].casefold() == title.strip().casefold():
        lines = lines[1:]
    return "".join(f"<p>{escape(line)}</p>" for line in lines)


def extract_article(
    url: str, html: str, fetched_at: datetime | None = None
) -> Extracted:
    """Build a Document from `url`'s fetched HTML.

    fetched_at is the fallback publication date when the page carries
    none of its own - the moment it was actually fetched, matching
    extract.py's own datetime.now(UTC) fallback for a mail message with
    no Date header. Defaults to "now" at call time when not given.

    Raises ExtractionError when trafilatura finds nothing worth printing:
    an error page, a login wall with no readable body at all, or HTML its
    own heuristics do not recognise as an article. A hard paywall that
    returns a short but real teaser does NOT raise here - that is a
    judgement call for the caller (phase8-urls.md's first risk), which
    has the word_count this function reports to make it with.
    """
    extracted = trafilatura.bare_extraction(
        html, url=url, with_metadata=True, favor_precision=True
    )
    if extracted is None or not (extracted.text or "").strip():
        raise ExtractionError(f"no article content found at {url}")

    title = extracted.title or url
    document = Document(
        origin=Origin(kind="url", identifier=url),
        publication=_publication(url, extracted.sitename),
        title=title,
        author=extracted.author or None,
        date=_parsed_date(extracted.date) or fetched_at or datetime.now(UTC),
        html=_body_html(extracted.text, title),
    )
    return Extracted(document=document, word_count=len(extracted.text.split()))
