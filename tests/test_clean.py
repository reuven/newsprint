from datetime import UTC, datetime
from pathlib import Path

import pytest

from newsprint.clean import clean_document
from newsprint.models import Document, Origin

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

    from newsprint.clean import _rendered_lines

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

    from newsprint.clean import _rendered_lines

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

    from newsprint.clean import _rendered_lines

    html = "<div>Real text<!--[if mso]>hidden mso markup<![endif]--><p>More.</p></div>"
    soup = BeautifulSoup(html, "lxml")
    assert _rendered_lines(soup.div) == ["Real text", "More."]


def test_block_level_siblings_are_separate_rendered_lines() -> None:
    """Two sibling <p> tags are two real, separate lines - the fix must
    not collapse genuine block-level structure along with the inline-tag
    fragmentation it removes."""
    from bs4 import BeautifulSoup

    from newsprint.clean import _rendered_lines

    html = "<div><p>First paragraph.</p><p>Second paragraph.</p></div>"
    soup = BeautifulSoup(html, "lxml")
    assert _rendered_lines(soup.div) == ["First paragraph.", "Second paragraph."]


def test_rendered_lines_of_an_empty_tag_is_empty() -> None:
    from bs4 import BeautifulSoup

    from newsprint.clean import _rendered_lines

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

    from newsprint.boilerplate import content_ratio
    from newsprint.clean import _rendered_lines

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

    from newsprint.clean import _strip_trailing_chrome_run

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

    from newsprint.clean import _strip_leading_chrome_run

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

    from newsprint.clean import _is_standalone_line

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

    from newsprint.clean import _strip_duplicate_title_block

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
    """Pure smoke test over the live, regenerated corpus: nothing may
    raise. This is deliberately the only thing asserted here.

    An earlier version of this test also pinned specific *content*
    surviving in specific named fixtures (a standalone episode dateline,
    a Chinese-headline dateline, a sign-off protected by the Puck FAQ
    paragraph - see git history if you want the exact strings). Those
    pins broke twice for a reason that had nothing to do with a
    regression: `tests/fixtures/` is regenerated from the user's live
    Thunderbird mbox by `make fixtures`, and that mailbox changes - new
    mail arrives, and this tool retires printed messages out of the
    folder - so the named fixtures could simply stop existing. The
    regressions those pins guarded against are real and are now covered
    by synthetic, fixture-independent tests instead, which reproduce the
    exact structural shape without depending on any particular message
    still being on disk: test_a_short_metadata_line_survives_the_bare_
    cta_line_that_follows_it and test_the_same_shape_survives_with_a_cjk_
    headline (both just below) for the dateline/short-heading guard, and
    test_a_full_sentence_footer_paragraph_protects_the_sign_off_above_it
    (also just below) for the trailing-walk stopping point. See those
    tests' docstrings for the full history each one carries forward from
    this one.
    """
    from newsprint.extract import extract

    paths = sorted(FIXTURES.glob("*.eml"))
    assert paths, "no fixtures; run `make fixtures`"
    for path in paths:
        clean_document(extract(path.read_bytes()))


def test_a_short_metadata_line_survives_the_bare_cta_line_that_follows_it() -> None:
    """Regression guard for a real bug (see test_cleaning_every_fixture_
    never_raises for the history): a manual recount of the fixture corpus
    once found the chrome stripper destroying more real content than
    chrome, because a heading-only block - a masthead, a subtitle, a
    dateline - scores exactly 0.00 under content_ratio, the same score as
    a link roundup, so no ratio threshold could ever have spared it.

    This reproduces the exact shape that surfaced it: a podcast
    newsletter's own bare episode-metadata dateline (a duration, no
    sentence-ending punctuation - so is_boilerplate_line's short-line
    fallback scores it 0.00, same as a link-roundup item), immediately
    followed by a bare "Listen now" call-to-action line - the CTA-line
    half of this shape is documented in boilerplate.py's PHRASES comment
    ("LISTEN NOW"), found immediately after such a metadata line in five
    real publications' fixtures. The bare CTA line below is genuine,
    high-confidence chrome (an exact _FULL_LINE_CHROME entry) and must
    be removed; the metadata line above it matches no PHRASES or
    _FULL_LINE_CHROME entry, is short and unpunctuated, and must survive
    via _is_protected_heading's short-line guard - which, before the
    fix, nothing spared it from being treated exactly like the CTA line
    below it. (Deliberately does NOT end the dateline in a closing
    parenthesis, e.g. "(25 mins)" - that character is itself one of
    is_boilerplate_line's own sentence-ending markers, see
    boilerplate._SENTENCE_END, so a parenthesised duration would survive
    on content_ratio alone regardless of the guard this test exists to
    check, and silently stop testing anything.)
    """
    html = (
        "<html><body>"
        "<div><p>A real episode transcript paragraph, long enough and "
        "punctuated enough to read as genuine article prose rather than "
        "a caption or a label of any kind.</p></div>"
        "<div>Listen now, 25 min</div>"
        "<div>Listen now</div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Listen now, 25 min" in cleaned.html
    assert "<div>Listen now</div>" not in cleaned.html


def test_the_same_shape_survives_with_a_cjk_headline() -> None:
    """The same regression as test_a_short_metadata_line_survives_the_
    bare_cta_line_that_follows_it, but with a headline in Chinese
    appended to the dateline on the same line - the second, verified-
    absent-before-the-fix example the original fixture-corpus pin used,
    confirmed by actually running the pre-fix cleaner (commit 529d163)
    against the corpus. The assertion pins the *full* line, not just the
    Chinese portion: in the real corpus, the Chinese headline alone also
    appeared elsewhere, in a separate hidden preview snippet, so a
    substring pin on it alone could pass even when the defect this test
    guards against was present. Reproduced here with a duplicate of the
    headline text sitting elsewhere in the document (simulating that
    hidden preview snippet), so the same trap is still exercised.
    """
    html = (
        "<html><body>"
        '<div style="display:none">与运动员首次合作效果惊人</div>'
        "<div><p>A real episode transcript paragraph, long enough and "
        "punctuated enough to read as genuine article prose rather than "
        "a caption or a label of any kind.</p></div>"
        "<div>Listen now, 8 min | 与运动员首次合作效果惊人</div>"
        "<div>Listen now</div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Listen now, 8 min | 与运动员首次合作效果惊人" in cleaned.html
    assert "<div>Listen now</div>" not in cleaned.html


def test_a_full_sentence_footer_paragraph_protects_the_sign_off_above_it() -> None:
    """Round 4 (derive-chrome spec, 2026-09-07) regression guard, the same
    shape as the two tests above but found while deriving new chrome
    rules rather than while auditing existing ones. Adding "need help?"
    and "brand partnerships" to PHRASES (see boilerplate.py's own
    comment and test_need_help_brand_partnerships_and_watch_now_were_
    tried_and_reverted in test_boilerplate.py), or the whole FAQ/brand-
    partnerships sentence to _FULL_LINE_CHROME (see
    test_puck_faq_block_as_a_whole_line_was_also_tried_and_reverted,
    also in test_boilerplate.py), was tried and reverted for the same
    reason: doing either deletes the paragraph below that had been the
    trailing walk's stopping point - it reads as full sentences with
    real punctuation, so content_ratio scores it as genuine content, not
    chrome - so the walk would continue one leaf further back and
    destroy the real, unprotected sign-off right before it. A two-line
    sign-off like "Have a great weekend, / Jon" gets no structural
    protection of its own: _is_protected_heading's single-line exception
    only covers a block whose *entire* text is one line, and this one is
    two, each short and unpunctuated enough to score as chrome alone.

    This reproduces the shape directly: a full-sentence footer paragraph
    ("Questions about your subscription?...") that must stay classified
    as real content (not chrome, and matching no PHRASES or
    _FULL_LINE_CHROME entry - the same "declined" status the real
    "need help?"/"brand partnerships" wording has). Sharing one top-level
    container with the two-line sign-off matters too, not just adjacency:
    _strip_chrome_blocks judges a top-level block's content_ratio over
    its WHOLE text, so the footer sentence's real content dilutes the
    sign-off's own weak per-line score enough to spare the block outright
    - the same "one bad line among several good ones just lowers a
    score, never deletes anything" dilution the module's own docstring
    describes, here relied on to protect the sign-off directly, in
    addition to (not instead of) the footer sentence stopping the
    trailing walk before it would otherwise reach that sign-off as its
    own separate leaf. The exact wording here is illustrative, not the
    real Puck fixture text (which is gone along with the rest of the
    regenerated corpus) - only the shape matters.
    """
    html = (
        "<html><body>"
        "<div><p>A real closing article paragraph, long enough and "
        "punctuated enough to read as genuine prose about the subject "
        "at hand, not a caption or a footer line.</p></div>"
        "<div>"
        "<p>Have a great weekend,<br>Jon</p>"
        "<p>Questions about your subscription? Our support team is "
        "always glad to help you sort out any issues.</p>"
        "</div>"
        "<div><p>&copy; 2026 Example Media LLC. All rights reserved.</p>"
        "</div>"
        "<div><p>Unsubscribe</p></div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Have a great weekend" in cleaned.html
    assert "Questions about your subscription" in cleaned.html
    assert "Unsubscribe" not in cleaned.html
    assert "All rights reserved" not in cleaned.html


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


# Phase 8 (charts.md, 2026-09-07): keep an image the author introduced as
# evidence - a colon lead-in before it, or a Source/Chart/Figure/Fig./
# Credit/Data: caption after it - rather than dropping every image
# outright. The width gate is the same one _figure_placeholder_text
# already uses, reused rather than re-derived.
def test_a_colon_lead_in_keeps_a_wide_image() -> None:
    html = (
        "<html><body><div><p>Real prose sets up the argument, and in the "
        "best model scores between the two countries:</p>"
        '<img src="https://example.com/chart.png" width="600">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" in cleaned.html
    assert cleaned.images_kept == 1
    assert cleaned.images_dropped == ()


def test_a_source_caption_keeps_a_wide_image_with_no_colon_lead_in() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here, setting up context but not ending in a colon.</p>"
        '<img src="https://example.com/chart.png" width="600">'
        "<p>Source: Census Bureau</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" in cleaned.html
    assert cleaned.images_kept == 1


@pytest.mark.parametrize("caption", ["Source", "Chart", "Figure", "Fig.", "Credit"])
def test_each_caption_word_keeps_a_preceding_wide_image(caption: str) -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here, with no colon at the end of this sentence.</p>"
        '<img src="https://example.com/chart.png" width="600">'
        f"<p>{caption}: some attribution text.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 1


def test_bare_data_word_with_no_colon_does_not_count_as_a_caption() -> None:
    """`Data` is deliberately required to carry its own colon: the bare
    word alone starts a lot of ordinary prose ("Data suggests...") in a
    way "Source" or "Figure" at the very start of a caption line does
    not."""
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here, with no colon at the end of this sentence.</p>"
        '<img src="https://example.com/chart.png" width="600">'
        "<p>Data suggests the trend will continue.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 0


def test_data_with_a_colon_does_count_as_a_caption() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here, with no colon at the end of this sentence.</p>"
        '<img src="https://example.com/chart.png" width="600">'
        "<p>Data: Census Bureau, 2026.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 1


def test_a_here_s_the_chart_lead_in_with_no_trailing_colon_still_counts() -> None:
    html = (
        "<html><body><div><p>Real prose sets up the moment. Here's the "
        "chart, drawn from the same underlying dataset as before.</p>"
        '<img src="https://example.com/chart.png" width="600">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 1


def test_a_wide_image_with_neither_cue_is_still_dropped() -> None:
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here, ending in an ordinary period.</p>"
        '<img src="https://example.com/chart.png" width="600">'
        "<p>And more prose continues right after it, unrelated to a "
        "caption of any kind.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" not in cleaned.html
    assert cleaned.images_kept == 0
    assert len(cleaned.images_dropped) == 1


def test_a_narrow_image_with_a_colon_lead_in_is_still_dropped() -> None:
    """The width gate applies first, and independently of the lead-in -
    matching _figure_placeholder_text's own threshold and the design
    spec's "≥300px" wording exactly."""
    html = (
        "<html><body><div><p>Real prose sets up the argument, here's the "
        "chart:</p>"
        '<img src="https://example.com/chart.png" width="299">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" not in cleaned.html
    assert cleaned.images_kept == 0


def test_a_kept_image_gets_no_placeholder_even_with_data_reading_alt_text() -> None:
    """Kept and placeholder are mutually exclusive outcomes for the same
    image - a kept image is the real thing, so a text stand-in for it
    would be redundant, not an extra safety net."""
    html = (
        "<html><body><div><p>Real prose sets up the argument, here's the "
        "chart:</p>"
        '<img src="https://example.com/chart.png" width="600" '
        'alt="Unemployment trend by quarter">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" in cleaned.html
    assert "figure-placeholder" not in cleaned.html
    assert "[figure:" not in cleaned.html
    assert cleaned.images_kept == 1


def test_kept_image_survives_because_width_is_read_before_attrs_are_stripped() -> None:
    """The literal trap charts.md warns about: clean.py strips width and
    height from every retained tag (Puck's fixed 600px widths caused a
    real text-clipping bug), so the argument-figure rule must read
    `width` before that stripping runs - a rule that runs after it would
    silently never fire, dropping every image and reporting nothing
    wrong. Confirmed against clean_document's own source, not assumed:
    _strip_images (which calls _is_argument_figure) is called before
    _strip_presentational_attrs there. This test still exercises the
    order directly - not just the rule - by asserting BOTH that the
    image survived AND that its width attribute is gone from the
    output, which only happens if the width was read first and stripped
    after."""
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here, and here's the chart:</p>"
        '<img src="https://example.com/chart.png" width="600">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" in cleaned.html
    assert 'width="600"' not in cleaned.html
    assert cleaned.images_kept == 1


def test_a_kept_image_alone_in_its_cell_is_not_pruned_as_invisible() -> None:
    """A second trap, not the one charts.md names but the same shape:
    _prune_invisible_elements (round 3E) treats any element with no
    visible text as dead layout scaffolding - correct for a spacer or an
    already-image-stripped placeholder cell, but a kept chart image has
    no text of its own either, and neither does a <td> that wraps only
    that image. Without an explicit exception, this later, unrelated
    pass would quietly undo a keep decision _strip_images made
    correctly - not the rule failing to fire, but a downstream pass
    deleting its result anyway."""
    html = (
        "<html><body><div>"
        "<p>Real prose that continues for a good while here, setting up "
        "the argument the chart is about to make:</p>"
        '<table><tbody><tr><td><img src="https://example.com/chart.png" '
        'width="600"></td></tr></tbody></table>'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" in cleaned.html
    assert cleaned.images_kept == 1


@pytest.mark.skipif(not FIXTURES.exists(), reason="run `make fixtures` first")
def test_a_decorative_header_image_is_dropped_amid_several_kept_real_charts() -> None:
    """The specific issue the user reported: "Here's the chart:" followed
    by "Source: Census Bureau" with no chart in between - discovered
    against a real Noahpinion newsletter that mixed real data charts
    (each introduced by a colon-ending sentence or followed by a
    "Source:" caption) with one purely decorative post-header
    illustration ("Art by GPT-6") that carried neither cue: its own
    preceding text was "READ IN APP" (a real CTA phrase, but not a
    colon-ending lead-in), and its following text was only the caption
    itself, not a Source/Chart/Figure/... label.

    That fixture is gone along with the rest of the regenerated corpus
    (see test_cleaning_every_fixture_never_raises for why), so this
    reproduces the same shape synthetically: one decorative header image
    with neither cue, sitting among several real charts that each do
    carry one. The multi-image, single-document scale matters, not just
    each rule in isolation (already covered by test_a_colon_lead_in_
    keeps_a_wide_image and friends, above) - it is what the real report
    actually exercised, and what a change that broke the interaction
    between images sharing one document would show up in.
    """
    html = (
        "<html><body><div>"
        "<p>READ IN APP</p>"
        '<img src="https://example.com/decorative-header.png" width="600" '
        'alt="A whimsical illustration of a robot writing a newsletter">'
        "<p>Art by GPT-6</p>"
        "<p>Real prose sets up the first argument, comparing two datasets "
        "over the last decade:</p>"
        '<img src="https://example.com/chart-1.png" width="600">'
        "<p>Real prose continues, setting up a second comparison without "
        "ending in a colon this time.</p>"
        '<img src="https://example.com/chart-2.png" width="600">'
        "<p>Source: Census Bureau</p>"
        "<p>A third point, again ending with a colon before the figure:</p>"
        '<img src="https://example.com/chart-3.png" width="600">'
        "<p>More prose, this time captioned afterward instead:</p>"
        '<img src="https://example.com/chart-4.png" width="600">'
        "<p>Chart: Federal Reserve data.</p>"
        "<p>A fifth point, ending in a colon once more:</p>"
        '<img src="https://example.com/chart-5.png" width="600">'
        "<p>A sixth and final comparison, also ending in a colon:</p>"
        '<img src="https://example.com/chart-6.png" width="600">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    # The decorative header image must not survive as a kept <img>.
    assert "decorative-header.png" not in cleaned.html
    # Every real chart must survive as a kept <img>.
    for n in range(1, 7):
        assert f"chart-{n}.png" in cleaned.html
    assert cleaned.images_kept >= 6


def test_a_screenshot_captioned_only_with_a_parenthesised_link_is_kept() -> None:
    """Platformer's "Those good posts": each social-media screenshot is
    followed by nothing but a link to the original. The images carry no
    alt text and no lead-in colon, so both older gates missed them and
    the printout showed a column of bare "(Link)" lines where the joke
    had been."""
    html = (
        "<html><body><div>"
        "<h3>Those good posts</h3>"
        '<div class="kg-card kg-image-card">'
        '<img src="https://storage.ghost.io/shot.png" alt width="600" height="148">'
        "</div>"
        '<p>(<a href="https://example.com/post">Link</a>)</p>'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 1
    assert "storage.ghost.io/shot.png" in cleaned.html


def test_the_link_caption_survives_real_html_whitespace() -> None:
    """Newsletters arrive indented across lines, not minified. Reading
    the caption block without stripping leaves "\\n    ( Link )\\n  ",
    which matches nothing - so the screenshot would be dropped on every
    real message while a minified test fixture kept passing.
    """
    html = (
        "<html><body><div>\n"
        "  <h3>Those good posts</h3>\n"
        '  <div class="kg-card kg-image-card">\n'
        '    <img src="https://storage.ghost.io/shot.png" alt width="600">\n'
        "  </div>\n"
        "  <p>\n"
        '    (<a href="https://example.com/post">Link</a>)\n'
        "  </p>\n"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 1
    assert "storage.ghost.io/shot.png" in cleaned.html


def test_the_link_caption_still_obeys_the_width_gate() -> None:
    """An icon followed by the same caption is still an icon - the width
    gate is what keeps spacers and tracking pixels out, and this third
    caption shape must not become a way around it."""
    html = (
        "<html><body><div>"
        '<img src="https://example.com/icon.png" alt width="24">'
        '<p>(<a href="https://example.com/post">Link</a>)</p>'
        "</div></body></html>"
    )
    assert clean_document(document(html)).images_kept == 0


def test_a_link_inside_a_longer_caption_does_not_keep_the_image() -> None:
    """Only a caption that is *nothing but* the parenthesised link counts.
    A block that merely contains a link is ordinary prose, and matching it
    would readmit decorative images across the whole archive."""
    html = (
        "<html><body><div>"
        '<img src="https://example.com/promo.png" alt width="600">'
        '<p>Sponsored by Acme. (<a href="https://example.com/x">Link</a>) '
        "Read more about our offer today.</p>"
        "</div></body></html>"
    )
    assert clean_document(document(html)).images_kept == 0


def test_an_axios_style_footer_is_removed_whole() -> None:
    """The user's report: the tail of every Axios newsletter survived
    cleaning intact. Its lines are short declarative sentences, so each
    scored as content, and the last of them - the postal address - held
    the trailing chrome run open so that nothing was removed at all.
    """
    html = (
        "<html><body><div>"
        "<p>The Fed declined to move rates this month, which surprised almost "
        "nobody who had been paying attention to the minutes and the dot plot.</p>"
        "<p>Advertise with us.</p>"
        "<p>Axios thanks our partners for supporting our journalism.</p>"
        "<p>Sponsorship has no influence on editorial content.</p>"
        "<p>Thank you for signing up for this Axios newsletter.</p>"
        "<p>Follow Axios across:</p>"
        "<p>Axios, PO Box 101060, Arlington VA 22201</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    text = cleaned.html
    assert "surprised almost" in text, "the article itself must survive"
    for chrome in (
        "Advertise with us",
        "supporting our journalism",
        "Sponsorship has no influence",
        "Thank you for signing up",
        "Follow Axios across",
        "PO Box 101060",
    ):
        assert chrome not in text, f"still present: {chrome!r}"


def _sponsor_html(after: str) -> str:
    """An Axios-shaped sponsor block: the header and the ad's two blocks
    are sibling table rows, with `after` as the row that follows."""
    return (
        "<html><body><table>"
        "<tr><td><p>The Fed declined to move rates this month, which surprised "
        "almost nobody watching the minutes.</p></td></tr>"
        "<tr><td><p>A MESSAGE FROM AXIOS</p></td></tr>"
        "<tr><td><p>Media is shifting fast. Our reporters see it first.</p></td></tr>"
        "<tr><td><p>Sara Fischer and Kerry Flynn go deeper than the headlines, "
        "tracking the deals and disruptions that matter.</p></td></tr>"
        f"<tr><td><p>{after}</p></td></tr>"
        "</table></body></html>"
    )


def test_a_sponsor_block_is_removed_with_its_body() -> None:
    """ "A MESSAGE FROM OUR SPONSOR" was already chrome, but removing the
    header alone left the ad copy behind - which is what the user
    reported. Ad copy reads exactly like editorial prose to content_ratio,
    so the extent has to come from the shape, not a score."""
    cleaned = clean_document(
        document(_sponsor_html("2. Warsh's labor market calculus"))
    )
    assert "A MESSAGE FROM" not in cleaned.html
    assert "Media is shifting fast" not in cleaned.html
    assert "Sara Fischer" not in cleaned.html
    assert "surprised almost nobody" in cleaned.html, "the article must survive"
    assert "Warsh" in cleaned.html, "the next section heading must survive"


def test_a_numbered_section_heading_stops_the_sponsor_removal() -> None:
    """Axios numbers its sections, and in every fixture the ad ends right
    before one. That is the hard stop that keeps this pass from running
    out of an ad and into the article behind it."""
    cleaned = clean_document(document(_sponsor_html("2. Americans' job market views")))
    assert "Americans" in cleaned.html


def test_a_long_block_stops_the_sponsor_removal() -> None:
    """The second bound: an ad body runs 251-394 characters across the
    corpus, while the article blocks that follow one run 831-2430."""
    long_article = "The labor market firmed up this year. " * 22
    html = (
        "<html><body><table>"
        "<tr><td><p>A MESSAGE FROM AXIOS</p></td></tr>"
        f"<tr><td><p>{long_article}</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "A MESSAGE FROM" not in cleaned.html
    assert "labor market firmed up" in cleaned.html


def test_at_most_two_blocks_follow_a_sponsor_header_into_the_bin() -> None:
    """Even with nothing else to stop it, the block cap bounds the damage
    a differently-shaped ad could do."""
    html = (
        "<html><body><table>"
        "<tr><td><p>A MESSAGE FROM ACME</p></td></tr>"
        "<tr><td><p>Acme makes the finest anvils.</p></td></tr>"
        "<tr><td><p>Buy one today and save.</p></td></tr>"
        "<tr><td><p>Meanwhile the Fed said nothing at all.</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "finest anvils" not in cleaned.html
    assert "Buy one today" not in cleaned.html
    assert "Fed said nothing" in cleaned.html


def test_a_sponsor_header_with_nothing_after_it_is_left_to_the_line_pass() -> None:
    """No following sibling at any level means there is no ad body to
    find, so this pass declines and _strip_line_chrome removes the header
    on its own."""
    html = (
        "<html><body>"
        "<div><p>The Fed declined to move rates this month, which surprised "
        "almost nobody watching the minutes.</p></div>"
        "<div><p>A MESSAGE FROM OUR SPONSOR</p></div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "A MESSAGE FROM" not in cleaned.html
    assert "surprised almost nobody" in cleaned.html


def test_a_second_sponsor_header_inside_the_first_block_is_not_revisited() -> None:
    """When one sponsor block's two rows carry a second header away with
    them, that header's node is already detached by the time the loop
    reaches it."""
    html = (
        "<html><body><table>"
        "<tr><td><p>A MESSAGE FROM ONE</p></td></tr>"
        "<tr><td><p>Acme makes the finest anvils in the west.</p></td></tr>"
        "<tr><td><p>A MESSAGE FROM TWO</p></td></tr>"
        "<tr><td><p>The Fed declined to move rates this month, which "
        "surprised almost nobody watching the minutes.</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "A MESSAGE FROM" not in cleaned.html
    assert "finest anvils" not in cleaned.html
    assert "surprised almost nobody" in cleaned.html


def test_a_new_york_times_masthead_tail_is_removed() -> None:
    """The user's report: every Times newsletter ended with its masthead
    and subscription block. The columnist's own sign-off sits directly
    above it and must survive - that is where the trailing run has to
    stop."""
    html = (
        "<html><body><div>"
        "<p>Germany's coalition talks collapsed this week, which surprised "
        "almost nobody who had been watching the polls.</p>"
        "<p>Thanks for spending part of your morning with The Times and me. "
        "See you tomorrow. — Sam</p>"
        "<p>Reach our team at themorning@nytimes.com.</p>"
        "<p>Host: Sam Sifton</p>"
        "<p>News Staff: Evan Gorelick, Brent Lewis, Lara McCoy, Karl Russell</p>"
        "<p>Editorial Director, Newsletters: Jodi Rudoren</p>"
        "<p>Deputy Editorial Director: Lauren Jackson</p>"
        "<p>Subscribe to The TimesGet The New York Times app</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "coalition talks collapsed" in cleaned.html
    assert "Thanks for spending part of your morning" in cleaned.html, (
        "the sign-off is the author's own words, not chrome"
    )
    for chrome in (
        "Reach our team",
        "Jodi Rudoren",
        "Lauren Jackson",
        "Evan Gorelick",
        "New York Times app",
    ):
        assert chrome not in cleaned.html, f"still present: {chrome!r}"
