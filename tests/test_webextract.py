"""Turning a fetched web page's HTML into a Document (phase8-urls.md).

trafilatura reads the page's own rendered DOM, not its CSS, which is why a
soft paywall (the server sends the full article and hides it with CSS)
yields the full text with no special handling here - these tests use plain
static HTML fixtures, never a real fetch, matching the pattern every other
module in this project uses to keep the network out of the test suite.
"""

from datetime import UTC, datetime

import pytest

from shabbat_print.webextract import ExtractionError, extract_article

ARTICLE_HTML = """
<html>
<head>
<title>A Real Headline</title>
<meta name="author" content="Jane Doe">
<meta property="og:site_name" content="Example Times">
</head>
<body>
<nav>Home | World | Opinion | Subscribe</nav>
<article>
<h1>A Real Headline</h1>
<p>By Jane Doe</p>
<p>September 1, 2026</p>
<p>This is the first real paragraph of the article, containing enough
words to be plausible content for a real news article that someone would
want to print and read on Shabbat afternoon, well past any teaser
threshold this project cares about.</p>
<p>This is a second paragraph continuing the article with more
substantive discussion of the topic at hand, elaborating further on the
point already made above in rather a lot of unnecessary detail, just to
be sure the word count clears any reasonable bar.</p>
</article>
<footer>Subscribe now. Copyright 2026 Example Times. All rights reserved.
Unsubscribe from these emails.</footer>
</body>
</html>
"""

TEASER_HTML = """
<html><head><title>Behind the Paywall</title></head>
<body><article>
<h1>Behind the Paywall</h1>
<p>Subscribe to read the rest of this article.</p>
</article></body></html>
"""


def test_extracts_title_author_publication_and_date_from_metadata() -> None:
    result = extract_article("https://example.com/a", ARTICLE_HTML)
    document = result.document
    assert document.title == "A Real Headline"
    assert document.author == "Jane Doe"
    assert document.publication == "Example Times"
    assert document.date == datetime(2026, 9, 1, tzinfo=UTC)


def test_origin_is_a_url_with_no_uid() -> None:
    result = extract_article("https://example.com/a", ARTICLE_HTML)
    assert result.document.origin.kind == "url"
    assert result.document.origin.identifier == "https://example.com/a"
    assert result.document.origin.uid is None


def test_word_count_reflects_the_extracted_body() -> None:
    result = extract_article("https://example.com/a", ARTICLE_HTML)
    assert result.word_count > 40


def test_a_thin_extraction_reports_a_low_word_count() -> None:
    result = extract_article("https://example.com/paywalled", TEASER_HTML)
    assert result.word_count < 20


def test_falls_back_to_the_hostname_when_no_sitename_is_advertised() -> None:
    html = "<html><body><article><p>" + ("word " * 60) + "</p></article></body></html>"
    result = extract_article("https://www.example.org/piece", html)
    assert "example.org" in result.document.publication


def test_falls_back_to_fetched_at_when_the_page_carries_no_date() -> None:
    html = "<html><body><article><p>" + ("word " * 60) + "</p></article></body></html>"
    fetched_at = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    result = extract_article(
        "https://www.example.org/piece", html, fetched_at=fetched_at
    )
    assert result.document.date == fetched_at


def test_drops_a_leading_line_that_only_repeats_the_title() -> None:
    result = extract_article("https://example.com/a", ARTICLE_HTML)
    # render.py synthesises its own <h1>title</h1>; the extracted body must
    # not repeat it a second time as its own first line.
    assert "A Real Headline" not in result.document.html


def test_the_body_keeps_the_real_paragraphs() -> None:
    result = extract_article("https://example.com/a", ARTICLE_HTML)
    assert "first real paragraph" in result.document.html
    assert "second paragraph" in result.document.html


def test_raises_extraction_error_when_nothing_is_found() -> None:
    with pytest.raises(ExtractionError):
        extract_article("https://example.com/empty", "<html><body></body></html>")


def test_extraction_error_names_the_url() -> None:
    with pytest.raises(ExtractionError, match="https://example.com/empty"):
        extract_article("https://example.com/empty", "<html><body></body></html>")


def test_publication_falls_back_to_the_bare_url_when_no_host_can_be_parsed() -> None:
    """No sitename metadata AND no parseable hostname (e.g. a scheme-less,
    host-less string) - the last-resort fallback is the identifier itself,
    matching extract.py's own "address.partition(...) or 'unknown'" habit
    of never leaving publication blank."""
    from shabbat_print.webextract import _publication

    assert _publication("not-a-url", None) == "not-a-url"


def test_parsed_date_returns_none_for_a_malformed_date_string() -> None:
    from shabbat_print.webextract import _parsed_date

    assert _parsed_date("not a real date") is None
