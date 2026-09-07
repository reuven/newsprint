from datetime import UTC, datetime

from shabbat_print.models import Document, Origin
from shabbat_print.teaser import word_count


def document(html: str, title: str = "An Issue") -> Document:
    return Document(
        origin=Origin(kind="email", identifier="<x@example.com>"),
        publication="Test Weekly",
        title=title,
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=html,
    )


def test_word_count_counts_words_across_paragraphs() -> None:
    html = "<p>Hello there world.</p><p>Another short paragraph here.</p>"
    assert word_count(document(html)) == 7


def test_word_count_excludes_a_line_that_echoes_the_subject() -> None:
    """A long headline that merely repeats the subject must not itself
    inflate the count - it is the whole point of a teaser."""
    title = "Pete Hegseth Is a Wrecking Ball"
    html = f"<h1>{title}</h1><p>Read more online.</p>"
    assert word_count(document(html, title=title)) == 3  # "Read more online."


def test_word_count_ignores_punctuation_differences_from_the_subject() -> None:
    """The subject header and the body's own rendered headline often use
    different quote characters or trailing punctuation - normalisation
    must still recognise them as the same line."""
    title = "It's Almost Here"
    html = "<h1>It’s Almost Here!</h1><p>One more paragraph.</p>"
    assert word_count(document(html, title=title)) == 3  # "One more paragraph."


def test_word_count_does_not_exclude_a_subtitle_that_merely_resembles_the_title() -> (
    None
):
    """Only an exact (normalised) match is excluded - a genuine subtitle,
    byline, or a section name that happens to be a prefix of the subject
    must still count toward the total."""
    title = "The GOP is MIA in North Carolina"
    html = f"<h1>{title}</h1><h2>A special report from the campaign trail</h2>"
    # The h2 subtitle is not the subject, so its 7 words are counted.
    assert word_count(document(html, title=title)) == 7


def test_a_real_teaser_falls_well_under_the_default_threshold() -> None:
    """David French's flagged example: headline, subtitle, byline, and a
    'View in browser' link - clean.py already strips the CTA boilerplate,
    leaving only editorial text, which is still far short of a real
    article."""
    title = "Pete Hegseth Is a Wrecking Ball"
    html = (
        f"<h1>{title}</h1>"
        "<h2>The pattern of dishonesty at the Pentagon continues.</h2>"
        "<p>David French</p>"
    )
    assert word_count(document(html, title=title)) < 250


def test_a_real_article_clears_the_default_threshold() -> None:
    paragraph = "This is a real sentence of substantive editorial content. " * 40
    html = f"<h1>Some Headline</h1><p>{paragraph}</p>"
    assert word_count(document(html, title="Some Headline")) >= 250
