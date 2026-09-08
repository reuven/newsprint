"""Decide whether a cleaned document is only a headline and a link.

Some publications, notably the NYT family, send a teaser: headline,
subtitle, "View in browser", byline, and nothing else. clean.py's chrome
removal leaves a teaser alone - the headline and byline are real editorial
text, not boilerplate - but it still occupies a whole cell for a sentence
or two.

word_count() measures what survives cleaning, so pipeline.py can compare it
against config.packet.min_words and skip a document that is not worth a
whole cell. A long headline must not be able to lift a teaser over the
threshold by itself, so any line whose normalised text matches the subject
is excluded from the count - exactly the case that flagged this feature:
David French's "Pete Hegseth Is a Wrecking Ball" is a 32-character headline
that would otherwise inflate a near-empty cell into looking substantial.

Measured against the live queue, the gap is unambiguous: the largest teaser
(124 words) and the smallest real article (514 words) are separated by
nearly 400 words, so the default threshold (see config.py) needs no
tuning - see
.superpowers/sdd/2026-09-07-newsletter-pipeline/title-and-skip.md.
"""

import re

from bs4 import BeautifulSoup

from .models import Document

_PUNCTUATION = re.compile(r"[^\w]+")


def _normalize(text: str) -> str:
    return _PUNCTUATION.sub("", text).casefold()


def word_count(document: Document) -> int:
    """Words in the cleaned body, excluding any line whose normalised text
    equals the subject (Document.title).

    Each of BeautifulSoup's stripped_strings is one rendered text node -
    in the semantic HTML clean.py produces, that is a paragraph, heading,
    or list item, which is exactly the granularity "a line that echoes the
    subject" means: a genuine subtitle or byline that merely resembles the
    subject is not an exact match once normalised, and still counts.
    """
    soup = BeautifulSoup(document.html, "lxml")
    normalized_title = _normalize(document.title)
    total = 0
    for text in soup.stripped_strings:
        if normalized_title and _normalize(text) == normalized_title:
            continue
        total += len(text.split())
    return total
