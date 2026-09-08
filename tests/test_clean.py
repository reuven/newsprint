from datetime import UTC, datetime
from pathlib import Path

import pytest

from shabbat_print.clean import clean_document
from shabbat_print.models import Document, Origin

FIXTURES = Path(__file__).parent / "fixtures"


def document(
    html: str,
    title: str = "An Issue",
    author: str | None = None,
    publication: str = "Test Weekly",
) -> Document:
    return Document(
        origin=Origin(kind="email", identifier="<x@example.com>"),
        publication=publication,
        title=title,
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=html,
        author=author,
    )


NEWSLETTER = """
<html><head><style>p { color: red }</style></head><body>
  <div><a href="https://example.com/view">View this email in your browser</a></div>
  <div>
    <h1>The Fed Declines Again</h1>
    <p>The Federal Reserve declined to move rates this month, which surprised
       almost nobody who had been paying attention to the minutes.</p>
    <p>What happens next is the interesting part, and it turns on a detail
       buried in a footnote that almost no one has read carefully.</p>
  </div>
  <div>
    <a href="https://example.com/a">Read more</a><br>
    <a href="https://example.com/b">Another link</a><br>
    <a href="https://example.com/c">And a third</a>
  </div>
  <div>
    <p>You are receiving this because you signed up.</p>
    <p><a href="https://example.com/u">Unsubscribe</a> |
       <a href="https://example.com/p">Manage your preferences</a></p>
    <p>© 2026 Test Weekly. All rights reserved.</p>
  </div>
</body></html>
"""


def test_content_survives() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "The Fed Declines Again" in cleaned.html
    assert "surprised" in cleaned.html
    assert "buried in a footnote" in cleaned.html


def test_trailing_chrome_is_removed() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "Unsubscribe" not in cleaned.html
    assert "All rights reserved" not in cleaned.html
    assert "You are receiving this" not in cleaned.html


def test_trailing_link_roundup_is_removed() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "Another link" not in cleaned.html


def test_leading_chrome_is_removed() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "View this email" not in cleaned.html


def test_style_and_script_are_removed() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "color: red" not in cleaned.html


def test_images_are_dropped_and_recorded() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/pixel.gif" width="1" height="1">'
        '<img src="https://example.com/chart.png" width="600">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" not in cleaned.html
    assert cleaned.images_kept == 0
    assert {dropped.src for dropped in cleaned.images_dropped} == {
        "https://example.com/pixel.gif",
        "https://example.com/chart.png",
    }


def test_wide_data_image_gets_a_text_placeholder() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/chart.png" width="600" '
        'alt="US-China trade balances as a percent of GDP, 2010-2026">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" not in cleaned.html
    assert (
        "[figure: US-China trade balances as a percent of GDP, 2010-2026]"
        in cleaned.html
    )
    assert 'class="figure-placeholder"' in cleaned.html


def test_narrow_data_image_is_dropped_with_no_placeholder() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/chart.png" width="299" '
        'alt="US-China trade balances as a percent of GDP">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" not in cleaned.html
    assert "figure-placeholder" not in cleaned.html
    assert "[figure:" not in cleaned.html


def test_wide_image_at_exactly_the_threshold_gets_a_placeholder() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/chart.png" width="300" '
        'alt="Unemployment trend by quarter">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "figure-placeholder" in cleaned.html


def test_decorative_image_alt_text_is_dropped_with_no_placeholder() -> None:
    """A wide, alt-bearing image is not enough on its own - the alt text
    must also read as data. This is the narrow rule the user chose over a
    looser one (width + alt length alone), which the corpus showed
    produces mostly decorative and machine-generated clutter."""
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/burger.png" width="600" '
        'alt="Illustration of a burger with star-shaped pickles">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "figure-placeholder" not in cleaned.html


def test_image_with_no_alt_text_is_dropped_with_no_placeholder() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/chart.png" width="600">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "figure-placeholder" not in cleaned.html


def test_image_with_unparseable_width_is_dropped_with_no_placeholder() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/chart.png" width="auto" '
        'alt="GDP growth chart">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "figure-placeholder" not in cleaned.html


@pytest.mark.parametrize(
    "alt_text",
    [
        "Ad",
        "Filled Star",
        "Share on Facebook",
        "Tweet this Story",
        "Post to LinkedIn",
        "Email this Story",
        "Text this Story",
        "Start writing",
        "Article Image",
    ],
)
def test_known_chrome_alt_text_is_excluded_even_when_wide(alt_text: str) -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        f'<img src="https://example.com/x.png" width="600" alt="{alt_text}">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "figure-placeholder" not in cleaned.html


@pytest.mark.parametrize(
    "alt_text",
    [
        "Illustration of bar chart columns forming a briefcase.",
        (
            "Animated illustration of a pair of sparkles moving up a stock "
            "chart, leaving trendlines behind them."
        ),
    ],
)
def test_a_decorative_illustration_is_excluded_even_when_it_mentions_a_chart(
    alt_text: str,
) -> None:
    """Found in the fixture corpus (round 1 of the "verify" pass): Axios's
    own section-banner illustrations describe themselves in words like
    "bar chart" or "stock chart" purely as decorative motifs, not as a
    real data figure - self-describing as an "illustration" is a stronger
    and more reliable signal of decorative art than the presence of a data
    word is a signal of real data, so it overrides a data-term match."""
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        f'<img src="https://example.com/x.png" width="600" alt="{alt_text}">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "figure-placeholder" not in cleaned.html


def test_a_bare_generic_alt_label_is_excluded() -> None:
    """Found in the fixture corpus: an alt text that is nothing but the
    generic category word itself ("Figure") carries no information beyond
    what "Article Image" already carries, and is excluded on the same
    grounds - a name for the *kind* of thing an image is, not a
    description of what it shows."""
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/x.png" width="600" alt="Figure">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "figure-placeholder" not in cleaned.html


def test_bare_publication_name_alt_text_is_excluded() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/logo.png" width="600" alt="Test Weekly">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html, publication="Test Weekly"))
    assert "figure-placeholder" not in cleaned.html


def test_long_alt_text_is_truncated_with_an_ellipsis() -> None:
    long_caption = (
        "A very long chart caption describing quarterly GDP growth trends "
        "across a dozen different economies over the past two decades in "
        "exhaustive, unnecessary detail"
    )
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        f'<img src="https://example.com/chart.png" width="600" alt="{long_caption}">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "…" in cleaned.html
    assert long_caption not in cleaned.html


def test_placeholder_still_records_a_dropped_image() -> None:
    """A placeholder is a presentation choice, not an exemption from the
    same reporting every other dropped image gets - a wrong call here must
    stay just as visible as a wrong chrome removal."""
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here.</p>"
        '<img src="https://example.com/chart.png" width="600" '
        'alt="Inflation by sector">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 0
    assert len(cleaned.images_dropped) == 1
    assert cleaned.images_dropped[0].src == "https://example.com/chart.png"


def test_placeholder_survives_the_trailing_chrome_walk() -> None:
    """The placeholder sits at the very end of the document - the same
    position a real trailing-chrome run gets removed from - and must
    survive: it is short, single-line, and not itself a chrome phrase (see
    clean.py's _is_protected_heading)."""
    html = (
        "<html><body><div>"
        "<p>The Federal Reserve declined to move rates this month, which "
        "surprised almost nobody who had been paying attention.</p>"
        '<img src="https://example.com/chart.png" width="600" '
        'alt="GDP growth chart">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "[figure: GDP growth chart]" in cleaned.html


def test_long_placeholder_also_survives_at_the_document_end() -> None:
    html = (
        "<html><body><div>"
        "<p>The Federal Reserve declined to move rates this month, which "
        "surprised almost nobody who had been paying attention.</p>"
        '<img src="https://example.com/chart.png" width="600" '
        'alt="US-China trade balances as a percent of GDP, 2010-2026">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert (
        "[figure: US-China trade balances as a percent of GDP, 2010-2026]"
        in cleaned.html
    )


def test_style_attribute_is_stripped() -> None:
    """Sender layout CSS must not leak into a page we typeset ourselves.

    Fixed pixel widths from a sender's template (commonly ~600px, meant for
    a browser window) overflow the printed cell and are the mechanism
    behind clipped text when fixed-width, overflow-hidden layout survives
    into the printed page.
    """
    html = (
        '<html><body><div><p style="width:600px">Real prose that continues '
        "for a good while here, well past the short-line cutoff.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "style=" not in cleaned.html


def test_table_wrapped_content_is_found() -> None:
    """Email HTML nests content inside layout tables; the cleaner must descend."""
    html = (
        "<html><body><table><tr><td><table><tr><td>"
        "<p>The Federal Reserve declined to move rates this month, which "
        "surprised almost nobody.</p>"
        "<p>Unsubscribe</p>"
        "</td></tr></table></td></tr></table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Federal Reserve" in cleaned.html
    assert "Unsubscribe" not in cleaned.html


def test_an_all_chrome_document_comes_back_empty() -> None:
    html = "<html><body><div><p>Unsubscribe</p></div></body></html>"
    assert clean_document(document(html)).html.strip() == ""


# Part B of the 2026-09-07 derive-chrome spec: get_text("\n", strip=True)
# inserts a separator between every text fragment bs4 finds, including
# fragments split apart only by an inline tag like <em>/<a>/<strong> - so a
# single flowing sentence like "...in France, and later in Spain" was being
# torn into several pseudo-"lines" ("in France", ", and later", "in Spain")
# purely because of inline markup, well before is_boilerplate_line ever
# sees it. _rendered_lines is the fix: lines split at true block
# boundaries (a block-level tag, or an explicit <br>) as the existing
# _is_line_boundary predicate already defines for _is_standalone_line,
# never at an inline tag's own start or end.
def test_inline_tags_do_not_fragment_a_rendered_line() -> None:
    from bs4 import BeautifulSoup

    from shabbat_print.clean import _rendered_lines

    html = (
        "<div><p>The launch happened first <em>in France</em>, and later "
        '<a href="https://example.com/x">in Spain</a>, before spreading '
        "everywhere else.</p></div>"
    )
    soup = BeautifulSoup(html, "lxml")
    assert _rendered_lines(soup.div) == [
        (
            "The launch happened first in France, and later in Spain, "
            "before spreading everywhere else."
        ),
    ]


def test_a_br_still_starts_a_new_rendered_line() -> None:
    """Unlike an inline tag's own boundary, an explicit <br> is a genuine
    line break and must still split the text on either side of it."""
    from bs4 import BeautifulSoup

    from shabbat_print.clean import _rendered_lines

    html = "<div><p>Line one<br>Line two</p></div>"
    soup = BeautifulSoup(html, "lxml")
    assert _rendered_lines(soup.div) == ["Line one", "Line two"]


def test_an_html_comment_is_not_a_rendered_line() -> None:
    """An HTML comment - Outlook's own MSO conditional markup
    ("<!--[if mso]>...<![endif]-->") is common throughout the fixture
    corpus - never renders as visible text in any browser or in
    WeasyPrint, so it must not be treated as content here either.
    bs4's own get_text() already excludes Comment nodes by default (they
    are a NavigableString *subclass*, but not NavigableString itself, and
    get_text()'s default `types` restricts to {NavigableString, CData});
    _rendered_lines must match that, not just check `isinstance(...,
    NavigableString)`, which is also true for a Comment and would let
    conditional-comment markup leak into content_ratio scoring and
    scripts/derive_chrome.py's frequency counts alike."""
    from bs4 import BeautifulSoup

    from shabbat_print.clean import _rendered_lines

    html = "<div>Real text<!--[if mso]>hidden mso markup<![endif]--><p>More.</p></div>"
    soup = BeautifulSoup(html, "lxml")
    assert _rendered_lines(soup.div) == ["Real text", "More."]


def test_block_level_siblings_are_separate_rendered_lines() -> None:
    """Two sibling <p> tags are two real, separate lines - the fix must
    not collapse genuine block-level structure along with the inline-tag
    fragmentation it removes."""
    from bs4 import BeautifulSoup

    from shabbat_print.clean import _rendered_lines

    html = "<div><p>First paragraph.</p><p>Second paragraph.</p></div>"
    soup = BeautifulSoup(html, "lxml")
    assert _rendered_lines(soup.div) == ["First paragraph.", "Second paragraph."]


def test_rendered_lines_of_an_empty_tag_is_empty() -> None:
    from bs4 import BeautifulSoup

    from shabbat_print.clean import _rendered_lines

    html = "<div>   </div>"
    soup = BeautifulSoup(html, "lxml")
    assert _rendered_lines(soup.div) == []


def test_inline_fragmentation_no_longer_scores_a_sentence_as_chrome() -> None:
    """The concrete harm named in the derive-chrome spec, part B: because
    is_boilerplate_line treats any line under 40 characters without
    terminal punctuation as chrome, a sentence fragmented at inline tag
    boundaries produced several such short, unpunctuated "lines" purely as
    an artifact of markup - degrading content_ratio for a block that is,
    read as a whole, ordinary prose. This reproduces that mechanism
    directly and confirms the fix restores a content_ratio of 1.0 for text
    that is entirely real prose, however many inline tags interrupt it."""
    from bs4 import BeautifulSoup

    from shabbat_print.boilerplate import content_ratio
    from shabbat_print.clean import _rendered_lines

    names = ["Chen", "Okafor", "Silva", "Park", "Novak", "Haddad", "Liu"]
    links = ", ".join(f"<a>{name}</a>" for name in names)
    html = (
        f"<div><p>The report cites work from {links}, each of whom "
        "contributed a distinct strand of evidence to the final published "
        "study.</p></div>"
    )
    soup = BeautifulSoup(html, "lxml")
    text = "\n".join(_rendered_lines(soup.div))
    assert content_ratio(text) == pytest.approx(1.0)


def test_leaf_tags_are_kept_whole_not_fragmented() -> None:
    """A paragraph must not be torn apart looking for its worst line.

    Placed among two other real paragraphs in the same containing block,
    the way an actual article body reads - not as the newsletter's *only*
    content. That distinction matters after the derive-chrome spec's part
    B fix (inline tags no longer fragment a line): content_ratio's "one
    bad line among several good lines just lowers a block's score, never
    deletes anything" promise (see is_definite_chrome_line's docstring)
    depends on there being several lines to dilute against. A newsletter
    whose *entire* body is this one paragraph was never covered by that
    promise - confirmed by hand, with no inline tags at all so B's fix
    changes nothing about it - and remains a known, documented gap (see
    the comment above _is_protected_heading) rather than something
    papered over here.
    """
    html = (
        "<html><body>"
        "<div><h1>Headline</h1></div>"
        "<div>"
        "<p>The opening paragraph lays out the news of the week in plenty "
        "of detail, giving readers enough context to follow along.</p>"
        "<p>The chapter closes on a long, thoughtful note "
        "about markets and memory, and if it moved you at all, there is an "
        '<a href="https://example.com/u">unsubscribe</a> link somewhere '
        "below, which almost nobody ever clicks.</p>"
        "<p>The newsletter then continues for several more paragraphs of "
        "real analysis, wrapping up with a closing thought about what "
        "comes next for readers who made it this far.</p>"
        "</div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "unsubscribe" in cleaned.html
    assert "markets and memory" in cleaned.html


def test_a_heading_only_block_survives_even_though_it_scores_as_chrome() -> None:
    """An h1-h6 block is never decomposed, however short its text scores.

    is_boilerplate_line's short-line fallback treats any line under 40
    characters without sentence punctuation as chrome, so a heading-only
    block scores exactly 0.00 - the same as a link-roundup item. No
    positive ratio threshold could ever spare it; the block must be
    protected structurally instead.
    """
    html = (
        "<html><body>"
        "<div><h1>Don't Call it a Cult</h1></div>"
        "<div><p>The rest of the newsletter goes on for a good while about "
        "the actual subject, which is substantial enough to read as a real "
        "article rather than a caption.</p></div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Don't Call it a Cult" in cleaned.html


def test_a_short_standalone_subtitle_survives() -> None:
    html = (
        "<html><body>"
        "<div>So far, so good</div>"
        "<div><p>The rest of the newsletter goes on for a good while about "
        "the actual subject, which is substantial enough to read as a real "
        "article rather than a caption.</p></div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "So far, so good" in cleaned.html


def test_a_masthead_with_byline_and_date_survives() -> None:
    html = (
        "<html><body>"
        "<div>The Reframe &middot; By A.R. Moxon &bull; 25 Apr</div>"
        "<div><p>The rest of the newsletter goes on for a good while about "
        "the actual subject, which is substantial enough to read as a real "
        "article rather than a caption.</p></div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "A.R. Moxon" in cleaned.html


def test_a_dateline_survives() -> None:
    html = (
        "<html><body>"
        "<div>2026-06-09 -- Tel Aviv</div>"
        "<div><p>The rest of the newsletter goes on for a good while about "
        "the actual subject, which is substantial enough to read as a real "
        "article rather than a caption.</p></div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Tel Aviv" in cleaned.html


def test_a_confident_chrome_block_is_still_removed_even_if_short() -> None:
    """The heading guard must not swallow a block a phrase match already
    confidently identifies as chrome, or test_an_all_chrome_document_
    comes_back_empty would start failing."""
    html = (
        "<html><body>"
        "<div><p>Unsubscribe</p></div>"
        "<div><p>The rest of the newsletter goes on for a good while about "
        "the actual subject, which is substantial enough to read as a real "
        "article rather than a caption.</p></div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Unsubscribe" not in cleaned.html


def test_dropped_blocks_are_reported() -> None:
    """A wrong removal must be visible, not invisible - the same principle
    trim.py already applies by naming every dropped cell."""
    cleaned = clean_document(document(NEWSLETTER))
    assert cleaned.blocks_dropped
    assert any("unsubscribe" in block.text.lower() for block in cleaned.blocks_dropped)


def test_trailing_chrome_nested_inside_a_protected_container_is_removed() -> None:
    """G1: the reported failure. A block-level heading guard spares the
    *whole* container that holds an h1, so chrome paragraphs sharing that
    container with the headline never reached _strip_chrome_blocks's
    per-block ratio check. The trailing-run pass must reach past the
    block boundary and remove them anyway, because they are still a
    genuine trailing suffix of the whole document.

    The wrapping <div> needs a sibling at the body level, or
    _content_root's single-child descent collapses past it and the
    headline+chrome group becomes ordinary top-level blocks that the
    existing block-level pass already handles on its own - which would
    not actually exercise the new nested case."""
    html = (
        "<html><body>"
        "<div><p>Also visible top-level content, just here so the real "
        "test group below is not the body's only child.</p></div>"
        "<div>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "<p>Unsubscribe</p>"
        "<p>1255 22nd St NW #18958, Washington, DC 20037</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Headline" in cleaned.html
    assert "genuine prose" in cleaned.html
    assert "Unsubscribe" not in cleaned.html
    assert "22nd St NW" not in cleaned.html


def test_trailing_run_never_reaches_past_a_genuine_final_paragraph() -> None:
    """The suffix rule's whole safety case: nothing before the last real
    content element is ever touched, however chrome-shaped it looks."""
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "<p>Read more below</p>"
        "<p>Another real closing paragraph with plenty of substance to "
        "read as genuine article prose rather than a link label.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Read more below" in cleaned.html
    assert "Another real closing paragraph" in cleaned.html


def test_trailing_run_stops_at_a_heading_even_mid_run() -> None:
    """A heading is genuine content, never chrome, however short it
    scores - the exact failure mode of the earlier reverted attempt."""
    html = (
        "<html><body><div>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "<h2>Don't Call it a Cult</h2>"
        "<p>Unsubscribe</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Don't Call it a Cult" in cleaned.html
    assert "Unsubscribe" not in cleaned.html


def test_trailing_run_guard_never_empties_a_nonempty_document() -> None:
    """If the whole document, walked from the end, never finds a genuine
    content element, the trailing run must remove nothing rather than
    silently vanish a newsletter that is entirely chrome."""
    from bs4 import BeautifulSoup

    from shabbat_print.clean import _strip_trailing_chrome_run

    html = "<div><p>Unsubscribe</p><p>&copy; 2026 Test Co</p></div>"
    soup = BeautifulSoup(f"<html><body>{html}</body></html>", "lxml")
    root = soup.body
    dropped = _strip_trailing_chrome_run(root)
    assert dropped == ()
    assert "Unsubscribe" in root.get_text()
    assert "Test Co" in root.get_text()


def test_trailing_run_dropped_elements_are_reported() -> None:
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "<p>Unsubscribe</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert any("unsubscribe" in block.text.lower() for block in cleaned.blocks_dropped)


def test_leading_chrome_run_is_removed() -> None:
    """F1: the leading-run rule mirrors the trailing one. 'Forwarded this
    email?' sits at the very start of several real newsletters, ahead of
    any content and nested inside a container the block-level pass cannot
    see into (the same nested-container shape G1 fixed for the trailing
    side)."""
    html = (
        "<html><body>"
        "<div><p>Also visible top-level content, just here so the real "
        "test group below is not the body's only child.</p></div>"
        "<div>"
        "<p>Forwarded this email? Subscribe here for more</p>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Forwarded this email" not in cleaned.html
    assert "Headline" in cleaned.html
    assert "genuine prose" in cleaned.html


def test_leading_run_never_reaches_past_a_genuine_first_paragraph() -> None:
    """The leading rule's whole safety case: nothing after the first real
    content element is ever touched, however chrome-shaped it looks."""
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "<p>Read more below</p>"
        "<p>Another real closing paragraph with plenty of substance to "
        "read as genuine article prose rather than a link label.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Headline" in cleaned.html
    assert "Read more below" in cleaned.html
    assert "Another real closing paragraph" in cleaned.html


def test_leading_run_stops_at_a_heading_even_mid_run() -> None:
    html = (
        "<html><body><div>"
        "<p>Unsubscribe</p>"
        "<h2>Don't Call it a Cult</h2>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Don't Call it a Cult" in cleaned.html
    assert "Unsubscribe" not in cleaned.html


def test_leading_run_guard_never_empties_a_nonempty_document() -> None:
    from bs4 import BeautifulSoup

    from shabbat_print.clean import _strip_leading_chrome_run

    html = "<div><p>Unsubscribe</p><p>&copy; 2026 Test Co</p></div>"
    soup = BeautifulSoup(f"<html><body>{html}</body></html>", "lxml")
    root = soup.body
    dropped = _strip_leading_chrome_run(root)
    assert dropped == ()
    assert "Unsubscribe" in root.get_text()
    assert "Test Co" in root.get_text()


def test_leading_run_dropped_elements_are_reported() -> None:
    html = (
        "<html><body><div>"
        "<p>Unsubscribe</p>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert any("unsubscribe" in block.text.lower() for block in cleaned.blocks_dropped)


# Round 2, F3: a definite-phrase line can be removed anywhere, not just at
# the leading or trailing edge - the position most of these phrases were
# actually found at in the live queue was the *middle* of the document,
# where neither directional rule can reach.
def test_mid_document_chrome_line_is_removed() -> None:
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "<p>A MESSAGE FROM OUR SPONSOR</p>"
        "<p>Another real paragraph with plenty of substance to read as "
        "genuine article prose rather than a link label or a caption.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "A MESSAGE FROM OUR SPONSOR" not in cleaned.html
    assert "genuine prose" in cleaned.html
    assert "Another real paragraph" in cleaned.html


def test_a_bare_inline_chrome_link_that_stands_alone_is_removed() -> None:
    """F2's confirmed finding: a bare <a>Unsubscribe</a> that is a sibling
    of block content (not embedded in a sentence) is invisible to
    _iter_text_elements - see the round-2 report's trace. F3 must reach it
    even though the trailing/leading passes structurally cannot."""
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "<table><tr><td><span>Get the Bulwark app</span></td></tr></table>"
        "<div>Copyright notice and a street address line here.</div>"
        '<a href="https://example.com/u">Unsubscribe</a>'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Unsubscribe" not in cleaned.html
    assert "Get the Bulwark app" not in cleaned.html
    assert "genuine prose" in cleaned.html


def test_an_inline_chrome_word_embedded_in_a_real_sentence_survives() -> None:
    """The adversarial case F3 exists to avoid: 'unsubscribe' as one word
    inside a real sentence, not a line by itself, must not be touched by
    _strip_line_chrome's whole-document pass - matches the existing
    leaf-level protection in test_leaf_tags_are_kept_whole_not_fragmented,
    including that test's same real-document shape (see its docstring for
    why a lone paragraph, with no other content around it, is a separate,
    documented gap rather than what this checks)."""
    html = (
        "<html><body>"
        "<div><h1>Headline</h1></div>"
        "<div>"
        "<p>The opening paragraph lays out the news of the week in plenty "
        "of detail, giving readers enough context to follow along.</p>"
        "<p>The chapter closes on a long, thoughtful note "
        "about markets and memory, and if it moved you at all, there is an "
        '<a href="https://example.com/u">unsubscribe</a> link somewhere '
        "below, which almost nobody ever clicks.</p>"
        "<p>The newsletter then continues for several more paragraphs of "
        "real analysis, wrapping up with a closing thought about what "
        "comes next for readers who made it this far.</p>"
        "</div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "unsubscribe" in cleaned.html
    assert "markets and memory" in cleaned.html


def test_standalone_line_check_of_a_detached_tag_is_false() -> None:
    """_strip_line_chrome always checks a candidate's `parent` is not None
    before calling this, so a genuinely parentless node never reaches it
    in practice - but the guard is real defense, not a stray branch, so it
    is exercised directly here rather than left untested."""
    from bs4 import BeautifulSoup

    from shabbat_print.clean import _is_standalone_line

    soup = BeautifulSoup("<a>Unsubscribe</a>", "lxml")
    tag = soup.a
    tag.extract()
    assert tag.parent is None
    assert _is_standalone_line(tag) is False


def test_a_bare_address_text_node_between_br_tags_is_removed() -> None:
    """F2's second confirmed finding, from the live queue: a footer block
    like '© 2026<br>Substack Inc.<br>548 Market Street...<br><a>Unsubscribe
    </a>' has its address as a bare, untagged text node sandwiched between
    two <br> tags, sharing a <p> with real sibling content ('© 2026',
    'Substack Inc.'). content_ratio treats the whole block as content
    (0.18, over CHROME_RATIO) because 'Substack Inc.' ends in a period and
    defeats the short-line fallback - and it can never be reached by
    position either, since it survives well inside the trailing run's
    stopping point. The address itself has no wrapping tag for
    _strip_line_chrome's tag-only walk to find, so it must also examine
    bare text nodes, using <br> (not just its parent's tag boundary) to
    tell 'its own line' apart from a real sibling on a different line."""
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "<p>© 2026<span>Some Publisher Inc.</span><br>"
        "548 Market Street PMB 72296, San Francisco, CA 94104<br>"
        '<a href="https://example.com/u">Unsubscribe</a></p>'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "548 Market Street" not in cleaned.html
    assert "Unsubscribe" not in cleaned.html
    assert "genuine prose" in cleaned.html


def test_a_bare_text_node_sharing_a_line_with_real_content_survives() -> None:
    """The adversarial case for the bare-text-node walk: even though the
    bare text node's own value is an exact phrase match ('Unsubscribe'),
    it shares its actual rendered line (the same <br>-delimited run,
    rather than its parent's whole child list) with a real sentence
    continuing right after it with no line break in between, so it must
    not be pulled out by _strip_line_chrome.

    Same real-document shape as test_leaf_tags_are_kept_whole_not_
    fragmented, and for the same reason (see that test's docstring): the
    paragraph sits among other real ones rather than being the
    newsletter's only content, so content_ratio's cross-line dilution -
    unrelated to the bare-text-node mechanism this test actually
    exercises - has real other lines to dilute against.
    """
    html = (
        "<html><body>"
        "<div><h1>Headline</h1></div>"
        "<div>"
        "<p>The opening paragraph lays out the news of the week in plenty "
        "of detail, giving readers enough context to follow along.</p>"
        "<p>Please read on.<br>Unsubscribe"
        "<span> is not the only way to leave a mailing list, our editor "
        "explained in detail near the end of the letter.</span></p>"
        "<p>The newsletter then continues for several more paragraphs of "
        "real analysis, wrapping up with a closing thought about what "
        "comes next for readers who made it this far.</p>"
        "</div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "is not the only way to leave a mailing list" in cleaned.html
    assert "Unsubscribe" in cleaned.html


def test_a_delimiter_separated_nav_row_across_inline_siblings_is_removed() -> None:
    """Round 3b, item 2: the real NYT structure - "View in browser" and
    "nytimes.com" as two separate <a> tags either side of a bare "|"
    <span>, all three sitting on one visual line with no <br> between
    them, inside one wrapping <div>. Neither <a> is individually
    'standalone' (each shares its line with the others), so the existing
    per-node candidate walk in _strip_line_chrome never offers "View in
    browser" alone to is_full_line_chrome - but the wrapping <div> IS a
    block-level candidate, and its own get_text(" ", strip=True) is
    exactly "View in browser | nytimes.com", which the extended
    is_full_line_chrome now matches. Decomposing that one <div> removes
    all three inline children together.

    Deliberately placed mid-document, nested one level inside a container
    it shares with a second real paragraph - the same shape as the live
    NYT fixture, where the nav row sits well past the opening headline
    and teaser. This is load-bearing, not decorative: with the nav row
    first and at the top level (tried first, and reverted after it was
    caught passing even with the boilerplate.py change reverted), the
    PRE-EXISTING ratio-based leading-run and top-level block passes
    already remove it on their own - "view in browser" is already a
    PHRASES substring, so its content_ratio alone is 0.0 - which would
    silently pass this test without exercising the new mechanism at all.
    Nesting it below real content and beside a second real paragraph
    keeps its containing block's overall ratio high (so the block-level
    pass spares it) and keeps it off both the leading and trailing runs
    (both stop at the real prose on either side of it first), so only the
    new is_full_line_chrome extension can remove it here."""
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real opening paragraph with enough content to read as genuine "
        "prose about the subject at hand, not a caption or a label.</p>"
        "<div>"
        '<div><a href="https://nl.nytimes.com/x">View in browser</a>'
        '<span style="margin:0 10px">|</span>'
        '<a href="https://nl.nytimes.com/y">nytimes.com</a></div>'
        "<p>A second real paragraph with plenty of its own substance, "
        "reading as genuine article prose rather than a link label.</p>"
        "</div>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "View in browser" not in cleaned.html
    assert "nytimes.com" not in cleaned.html
    assert "genuine prose" in cleaned.html
    assert "second real paragraph" in cleaned.html


def test_a_mixed_nav_row_with_one_real_content_segment_survives() -> None:
    """The safety property, exercised end to end: a nav row with one
    known-chrome segment ("View in browser") and one segment of real text
    (a headline, not a domain and not a known chrome phrase) must survive
    whole - requiring every segment to qualify is what keeps a genuine
    link among the chrome from being swept away with it. Same mid-document
    nesting as the removal test above, so this exercises the same
    candidate path and isn't spared for an unrelated, positional reason."""
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real opening paragraph with enough content to read as genuine "
        "prose about the subject at hand, not a caption or a label.</p>"
        "<div>"
        '<div><a href="https://example.com/x">View in browser</a>'
        '<span style="margin:0 10px">|</span>'
        "<span>The Fed considers new rate hikes this week</span></div>"
        "<p>A second real paragraph with plenty of its own substance, "
        "reading as genuine article prose rather than a link label.</p>"
        "</div>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "View in browser" in cleaned.html
    assert "The Fed considers new rate hikes this week" in cleaned.html
    assert "genuine prose" in cleaned.html
    assert "second real paragraph" in cleaned.html


def test_removing_a_line_cleans_up_an_emptied_parent() -> None:
    """Removing a line must not leave an empty parent element behind that
    renders as a blank gap on the printed page."""
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        '<div class="sponsor-wrap"><p>A MESSAGE FROM OUR SPONSOR</p></div>'
        "<p>Another real paragraph with plenty of substance to read as "
        "genuine article prose rather than a link label or a caption.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "sponsor-wrap" not in cleaned.html


def test_two_separate_matches_together_empty_their_shared_wrapper() -> None:
    """The wrapper's own combined text ('Restack Unsubscribe') is not
    itself a phrase match, so it survives its own candidacy check and is
    only removed once both of its children have been peeled off one at a
    time - exercising the cascade as an actual loop, not a one-step
    shortcut where the outermost matching tag already is the wrapper."""
    html = (
        "<html><body><div>"
        "<h1>Headline</h1>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        '<div class="chrome-wrap"><p>Restack</p><p>Unsubscribe</p></div>'
        "<p>Another real paragraph with plenty of substance to read as "
        "genuine article prose rather than a link label or a caption.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "chrome-wrap" not in cleaned.html
    assert "Restack" not in cleaned.html
    assert "Unsubscribe" not in cleaned.html
    assert "genuine prose" in cleaned.html
    assert "Another real paragraph" in cleaned.html


def test_other_document_fields_are_preserved() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert cleaned.title == "An Issue"
    assert cleaned.publication == "Test Weekly"
    assert cleaned.origin.identifier == "<x@example.com>"


# Round 3, section D: the duplicated title block. Substack repeats a
# post's own title/subtitle/author/date as a second header block a few
# lines into the body, on top of our own masthead+<h1> (rendered in
# render.py, not present in document.html at all). Reversing the earlier
# decision to leave this alone: the fixture corpus shows 14 of 15 live
# occurrences sit in the first 15 lines, so a position-gated rule is safe
# the same way the leading-run rule is.
def _duplicate_title_html(
    *, subtitle: str = "The subtitle line", extra_before: str = ""
) -> str:
    return (
        "<html><body><div>"
        f"{extra_before}"
        "<h2>Neo-Nazis and the Impotence of Trumponomics</h2>"
        f"<p>{subtitle}</p>"
        "<p>Paul Krugman</p>"
        "<p>Sep 7</p>"
        "<p>READ IN APP</p>"
        "<p>Crude economics doesn't explain what just happened, and here is "
        "a real paragraph of genuine article prose about the subject.</p>"
        "</div></body></html>"
    )


def test_duplicated_title_block_in_leading_region_is_removed() -> None:
    html = _duplicate_title_html()
    cleaned = clean_document(
        document(html, title="Neo-Nazis and the Impotence of Trumponomics")
    )
    assert "Neo-Nazis and the Impotence of Trumponomics" not in cleaned.html
    assert "The subtitle line" not in cleaned.html
    assert "Paul Krugman" not in cleaned.html
    assert "Sep 7" not in cleaned.html
    assert "Crude economics doesn't explain" in cleaned.html


def test_duplicate_title_match_is_a_prefix_or_truncation_in_either_direction() -> None:
    """Publications truncate: the body's own copy may be shorter (an
    ellipsis-truncated rendering) or the Subject may be shorter (a
    publication that adds a suffix in the body). Either direction of
    prefix must match, after case-folding and stripping punctuation and
    whitespace."""
    html = (
        "<html><body><div>"
        "<h2>What's new in DevEx - Septem</h2>"
        "<p>A subtitle</p>"
        "<p>Daniel Grechko</p>"
        "<p>Sep 2</p>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(
        document(html, title="What's new in DevEx - September 2 edition")
    )
    assert "What's new in DevEx" not in cleaned.html
    assert "genuine prose" in cleaned.html


def test_a_title_match_with_no_date_anchor_nearby_is_not_removed() -> None:
    """The load-bearing guard: a short line that merely happens to prefix
    the subject (a recurring section name like "Money Talks" or "Axios
    AM" in the live queue) is not, on its own, evidence of Substack's own
    duplicated header - only a short date line following within a few
    lines confirms that. Measured against the full fixture corpus: this
    guard is what correctly leaves newsletters-e-economist-com's "Money
    Talks" section name, and noreply-e-economist-com's "Cover Story", "The
    Insider" section names, and mike-axios-com's "Axios AM" masthead
    alone - none of them are followed by a bare date line."""
    html = (
        "<html><body><div>"
        "<h2>Money Talks</h2>"
        "<p>Dissecting the big themes in markets and the economy</p>"
        "<p>What the new trend means for the economy</p>"
        "<p>Don Weinland</p>"
        "<p>China business and finance editor</p>"
        "<p>Late last year I came across an amusing social-media post, and "
        "the whole thing turned out to be a genuine editorial paragraph.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html, title="Money Talks: Chinamaxxing GDP"))
    assert "Money Talks" in cleaned.html
    assert "Don Weinland" in cleaned.html
    assert "China business and finance editor" in cleaned.html


def test_the_real_headline_at_the_document_start_survives_a_later_duplicate() -> None:
    """The flagship regression case (from the project's own history: the
    reverted 095d0e5 change destroyed this exact headline). "Don't Call it
    a Cult" is the newsletter's own genuine headline at the very start of
    the body - it has no date line following it within the lookahead, so
    it must survive. A few lines later, Substack's own duplicated header
    block (title again, subtitle, author, date) DOES have a date anchor,
    and that one must be removed."""
    html = (
        "<html><body><div>"
        "<h2>Don't Call it a Cult</h2>"
        "<p>Forwarded this email? Subscribe here for more</p>"
        "<h2>Don't Call it a Cult</h2>"
        "<p>The Week in Conflict - June 1, 2026</p>"
        "<p>Ellie Power</p>"
        "<p>Jun 1</p>"
        "<p>READ IN APP</p>"
        "<p>It's one of the cardinal rules of cult deprogramming experts, "
        "and this is where the real article genuinely begins in earnest.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html, title="Don't Call it a Cult"))
    assert cleaned.html.count("Don't Call it a Cult") == 1
    assert "It's one of the cardinal rules" in cleaned.html


def test_duplicate_title_block_beyond_line_15_is_not_touched() -> None:
    """The one measured mid-document case (Ian Krietzberg, line 87 of a
    190-line digest) is a section heading naming one of several stories,
    not a duplicate - position-gating to the first 15 lines excludes it
    for free, without needing to tell the two apart by content."""
    padding = "".join(
        f"<p>Filler paragraph number {i} with enough real prose in it to "
        "read as genuine article content rather than a caption.</p>"
        for i in range(20)
    )
    html = (
        "<html><body><div>"
        f"{padding}"
        "<h2>A Late Section</h2>"
        "<p>A subtitle for the late section</p>"
        "<p>Some Author</p>"
        "<p>Sep 7</p>"
        "<p>More real article prose continues here at proper length.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html, title="A Late Section"))
    assert "A Late Section" in cleaned.html
    assert "A subtitle for the late section" in cleaned.html
    assert "Some Author" in cleaned.html


def test_duplicate_title_guard_never_empties_a_nonempty_document() -> None:
    from bs4 import BeautifulSoup

    from shabbat_print.clean import _strip_duplicate_title_block

    html = (
        "<div><h2>Just a Title</h2><p>A subtitle</p><p>Some Author</p>"
        "<p>Sep 7</p></div>"
    )
    soup = BeautifulSoup(f"<html><body>{html}</body></html>", "lxml")
    root = soup.body
    dropped = _strip_duplicate_title_block(root, document(html, title="Just a Title"))
    assert dropped == ()
    assert "Just a Title" in root.get_text()


def test_duplicate_title_block_dropped_elements_are_reported() -> None:
    cleaned = clean_document(
        document(
            _duplicate_title_html(), title="Neo-Nazis and the Impotence of Trumponomics"
        )
    )
    assert any("trumponomics" in block.text.lower() for block in cleaned.blocks_dropped)


@pytest.mark.skipif(not FIXTURES.exists(), reason="run `make fixtures` first")
def test_cleaning_every_fixture_never_raises() -> None:
    from shabbat_print.extract import extract

    paths = sorted(FIXTURES.glob("*.eml"))
    assert paths, "no fixtures; run `make fixtures`"
    cleaned_by_name = {}
    for path in paths:
        cleaned_by_name[path.name] = clean_document(extract(path.read_bytes()))

    # Content-retention regression guard. A manual recount of this corpus
    # once found the chrome stripper destroying more real content than
    # chrome: a heading-only block - a masthead, a subtitle, a dateline, a
    # headline in any script - scored exactly 0.00 under content_ratio,
    # the same score as a link roundup, so no ratio threshold could ever
    # have spared it. These two are pinned, verified-absent-before-the-fix
    # examples drawn from this exact corpus: a standalone episode
    # dateline, and an episode dateline with a headline in Chinese. Both
    # pins were confirmed by actually running the pre-fix cleaner
    # (commit 529d163) against this corpus: each full line is absent from
    # its pre-fix output and present in the current one. The mandarin pin
    # must be the *full* line, not just the Chinese portion - the Chinese
    # headline alone also appears pre-fix, in a separate hidden preview
    # snippet elsewhere in the message, so a substring pin on it alone
    # cannot fail on the defect it claims to guard against. If the
    # fixture corpus is regenerated and these particular newsletters
    # vanish, replace the pins rather than deleting the check.
    dickerson = "johnfdickerson-substack-com-oldest.eml"
    mandarin = "realtimemandarin-lessons-substack-com.eml"
    assert dickerson in cleaned_by_name, "fixture corpus changed: re-pin this test"
    assert mandarin in cleaned_by_name, "fixture corpus changed: re-pin this test"
    assert "Listen now (25 mins)" in cleaned_by_name[dickerson].html
    assert (
        "Listen now (8 mins) | 与运动员首次合作效果惊人"
        in cleaned_by_name[mandarin].html
    )

    # Round 4 (derive-chrome spec, 2026-09-07): a second, newer regression
    # guard, the same shape as the one above but found while deriving new
    # chrome rules rather than while auditing existing ones. Adding the
    # Puck FAQ/brand-partnerships paragraph to _FULL_LINE_CHROME (see
    # boilerplate.py's own comment and
    # test_puck_faq_block_as_a_whole_line_was_also_tried_and_reverted) was
    # tried, measured against the full fixture corpus, and reverted here
    # for the same reason: it deletes the paragraph that had been the
    # trailing walk's stopping point, so the walk continues one leaf
    # further back and destroys the real sign-off right before it. Pinned
    # against the live fixture, not just the unit-level check, so a
    # future change that reintroduces this by some other route is also
    # caught here.
    puck = "jon-puck-news.eml"
    assert puck in cleaned_by_name, "fixture corpus changed: re-pin this test"
    assert "Have a great weekend" in cleaned_by_name[puck].html


# Round 3, section E: elements with no visible text - a spacer/image-
# placeholder cell left empty once its image is stripped, or a
# publisher's own invisible preheader padding, used to control the
# preview snippet an inbox shows. Both occupy block space on the printed
# page (a tall blank gap, in the padding case) despite having nothing to
# show. get_text(strip=True) is not enough to detect either: bs4's strip
# only removes characters Python's str.isspace() recognises, which misses
# zero-width and other format characters (soft hyphen, zero-width space/
# joiner/non-joiner, word joiner, BOM) that real preheader padding is
# built from.
def test_an_element_with_only_invisible_characters_is_removed() -> None:
    html = (
        "<html><body><div>"
        '<div class="pad"> ‍ ‍</div>'
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "pad" not in cleaned.html
    assert "Real paragraph" in cleaned.html


def test_an_element_with_a_single_ordinary_character_survives() -> None:
    html = (
        "<html><body><div>"
        '<div class="mark">X</div>'
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert '<div class="mark">X</div>' in cleaned.html


def test_a_syntax_highlighted_space_span_survives() -> None:
    """The regression this project's own fixture corpus caught: syntax
    highlighting wraps each token in its own <span>, including the bare
    space between two of them. A first draft of section E treated
    ordinary whitespace the same as an invisible character and pruned
    that span, gluing "pip" and "install" together in
    mostly-python-ghost-io-oldest.eml. A lone space has real rendered
    width and must survive, however short it is and however "invisible"
    it might look to a naive check."""
    html = (
        "<html><body><div>"
        '<p><span style="color:navy">pip</span><span> </span>'
        '<span style="color:navy">install</span> gh-profiler and enough '
        "surrounding real prose to read as genuine article content.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<span> </span>" in cleaned.html
    assert "pipinstall" not in cleaned.html


def test_a_genuinely_empty_spacer_element_is_removed() -> None:
    """The spacer table is nested inside a block that also holds real
    prose (the way a layout table sits beside real content in the same
    cell in practice), so the pre-existing top-level "block has no text at
    all" cleanup in _strip_chrome_blocks - which only ever looks at
    root.children as a whole, never inside them - cannot be what accounts
    for this: content_root's own descent also cannot quietly drop it,
    since two real top-level siblings keep it from collapsing past this
    level. Only the new pass, walking every element rather than only
    leaves or top-level blocks, can reach it."""
    html = (
        "<html><body><div>"
        "<div>"
        '<table><tbody><tr><td class="spacer"></td></tr></tbody></table>'
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "</div>"
        "<div><p>Another real paragraph with plenty of substance to read "
        "as genuine article prose rather than a link label or a caption.</p>"
        "</div>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "spacer" not in cleaned.html
    assert "<table" not in cleaned.html
    assert "Real paragraph" in cleaned.html
    assert "Another real paragraph" in cleaned.html


def test_emptying_a_cell_cascades_up_through_its_layout_wrappers() -> None:
    """An image-only <td> left empty once its image is stripped must take
    its now-empty <tr>, <tbody> and <table> with it - the cascade the
    empty-parent cleanup in _strip_line_chrome already relies on for the
    same reason. A single pass is enough: get_text() is recursive, so the
    <table>'s own visibility already reflects its <td>'s, and decomposing
    the <table> takes the whole empty chain with it in one call."""
    html = (
        "<html><body><div>"
        "<div>"
        '<table><tbody><tr><td><img src="https://example.com/spacer.gif">'
        "</td></tr></tbody></table>"
        "<p>Real paragraph with enough content to read as genuine prose "
        "about the subject at hand, not a caption or a label.</p>"
        "</div>"
        "<div><p>Another real paragraph with plenty of substance to read "
        "as genuine article prose rather than a link label or a caption.</p>"
        "</div>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<table" not in cleaned.html
    assert "<tr" not in cleaned.html
    assert "Real paragraph" in cleaned.html


def test_br_and_hr_are_never_removed_for_having_no_text() -> None:
    """<hr> is nested inside a block that also holds real prose, rather
    than sitting as its own top-level element - a bare top-level <hr> is
    already decomposed by the pre-existing, unrelated "block has no text
    at all" branch in _strip_chrome_blocks, which is not what this test
    is about; nested here, only the new pass ever visits it at all, and
    it must leave it alone."""
    html = (
        "<html><body><div>"
        "<div><p>Real paragraph with enough content to read as genuine "
        "prose about the subject at hand, not a caption.<br>"
        "A second line after a real line break.</p>"
        "<hr>"
        "<p>More real prose continues here at proper length after the "
        "rule, still reading as genuine article content throughout.</p>"
        "</div>"
        "<div><p>Another real paragraph with plenty of substance to read "
        "as genuine article prose rather than a link label or a caption.</p>"
        "</div>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<br" in cleaned.html
    assert "<hr" in cleaned.html
