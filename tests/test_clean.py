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


# ---------------------------------------------------------------------------
# _strip_chrome_blocks sweeps root's own children. Reaching it and nothing
# else means putting the block mid-document: the leading and trailing runs
# walk in from the ends and stop at the first real content, and the line
# pass only takes lines that match a chrome phrase outright.
# ---------------------------------------------------------------------------

_BLOCK_A = (
    "<div><p>The Fed declined to move rates this month, which surprised "
    "almost nobody who had been watching the minutes closely.</p></div>"
)
_BLOCK_B = (
    "<div><p>Markets took the news calmly, which is not at all what anyone "
    "had forecast at the start of a week like this one.</p></div>"
)
# Chrome by its ratio of chrome lines to real ones, not by any single line
# being a known phrase - so the line pass cannot touch it and this pass has
# to be the one that does.
_CHROME_BLOCK = (
    "<div>"
    "<p>You are receiving this because you signed up at acme.com.</p>"
    "<p>Acme Inc, 12 Main Street, Springfield, IL 62704</p>"
    "<p>Update your email preferences at any time</p>"
    "</div>"
)


def test_the_report_of_a_removed_chrome_block_names_all_of_its_lines() -> None:
    """This pass takes a whole block on a score rather than a phrase, so
    the report of what it took is the only place a wrong removal shows.
    The block's lines are reported as they rendered, one per line."""
    cleaned = clean_document(
        document(f"<html><body>{_BLOCK_A}{_CHROME_BLOCK}{_BLOCK_B}</body></html>")
    )
    assert [block.text for block in cleaned.blocks_dropped] == [
        (
            "You are receiving this because you signed up at acme.com.\n"
            "Acme Inc, 12 Main Street, Springfield, IL 62704\n"
            "Update your email preferences at any time"
        )
    ]


def test_text_between_blocks_does_not_end_the_block_sweep() -> None:
    """Root's children are not all elements: a stray text node between two
    of them is common, and skipping one is not a reason to stop looking at
    the blocks behind it."""
    html = f"<html><body>{_BLOCK_A}  stray  {_CHROME_BLOCK}{_BLOCK_B}</body></html>"
    cleaned = clean_document(document(html))
    assert "Update your email preferences" not in cleaned.html
    assert "took the news calmly" in cleaned.html


def test_a_textless_block_is_removed_unless_it_holds_an_image() -> None:
    """A block with no text is layout scaffolding and goes. Since phase 8
    that is qualified: a kept chart has no text of its own either, so the
    exemption is for an <img>, not for having any descendant at all - a
    spacer's <br> is a descendant too."""
    lead_in = (
        "<div><p>Real prose sets up the argument, and in the best model "
        "scores between the two countries:</p></div>"
    )
    html = (
        "<html><body>"
        f"{lead_in}"
        '<div><img src="https://example.com/chart.png" width="600"></div>'
        "<div><br></div>"
        f"{_CHROME_BLOCK}{_BLOCK_B}</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" in cleaned.html, "the chart's own block is spared"
    assert "<br" not in cleaned.html, "the spacer's is not"
    # Sparing the chart is a reason to move to the next block, not to stop.
    assert "Update your email preferences" not in cleaned.html
    assert "took the news calmly" in cleaned.html


def test_a_protected_heading_does_not_end_the_block_sweep() -> None:
    """A heading and a sign-off are both spared, and sparing one is not a
    reason to stop looking at what comes after it."""
    html = (
        "<html><body>"
        f"{_BLOCK_A}"
        "<div><h2>What we are watching</h2></div>"
        f"{_CHROME_BLOCK}"
        f"{_BLOCK_B}"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "What we are watching" in cleaned.html
    assert "Update your email preferences" not in cleaned.html


def test_a_block_exactly_at_the_chrome_ratio_is_kept() -> None:
    """0.15 is the highest share of real content a block may have and
    still count as all chrome, not the lowest share that saves it. A
    footer landing exactly on the line keeps its sentence.

    None of these lines is a chrome phrase in its own right, so the line
    pass leaves them all standing and the block still reads as it was
    written when its ratio is taken.
    """
    chrome = [
        "Manage your preferences",
        "Copyright 2026 Acme Inc",
        "You received this because you signed up",
        "Update your email preferences",
        "Sent to you by Acme",
        "Add us to your address book",
        "Acme Inc, 12 Main Street, Springfield",
        "Was this forwarded to you?",
        "You can update your details here",
    ]
    content = "Rates held steady, and the chair said little."
    # 45 characters of content against 255 of chrome: 0.15 on the nose.
    assert len(content) / (len(content) + sum(len(line) for line in chrome)) == 0.15
    block = (
        "<div>" + "".join(f"<p>{line}</p>" for line in [content, *chrome]) + "</div>"
    )
    cleaned = clean_document(
        document(f"<html><body>{_BLOCK_A}{block}{_BLOCK_B}</body></html>")
    )
    assert "Rates held steady" in cleaned.html


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


# ---------------------------------------------------------------------------
# The two directional runs. To reach one and only one, the chrome has to
# sit inside a container the block sweep keeps whole, on lines that are not
# chrome phrases in their own right - so neither the block pass nor the
# line pass can be the one that took it.
# ---------------------------------------------------------------------------

_RUN_LEAD = "<p>Was this forwarded to you?<br>Sign up here</p>"
_RUN_TAIL = "<p>Acme Inc, 12 Main Street<br>Springfield, IL 62704</p>"
_RUN_ARTICLE = (
    "<h1>Headline</h1>"
    "<p>Real paragraph with enough content to read as genuine prose about "
    "the subject at hand, not a caption or a label.</p>"
)
_RUN_ARTICLE_2 = (
    "<p>Second real block of prose here, long enough to be an article "
    "paragraph in its own right.</p>"
)


def test_both_runs_report_the_lines_they_took() -> None:
    """A run walks in from one end and stops at the first real content, so
    what it takes is bounded but not named in advance - which makes the
    report of it the only account the reader gets. A leaf's own <br> lines
    are reported as the lines they render as."""
    html = (
        "<html><body>"
        f"<div>{_RUN_LEAD}{_RUN_ARTICLE}</div>"
        f"<div>{_RUN_ARTICLE_2}{_RUN_TAIL}</div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "forwarded to you" not in cleaned.html
    assert "Springfield" not in cleaned.html
    assert "Real paragraph with enough content" in cleaned.html
    assert "Second real block of prose" in cleaned.html
    assert [block.text for block in cleaned.blocks_dropped] == [
        "Was this forwarded to you?\nSign up here",
        "Acme Inc, 12 Main Street\nSpringfield, IL 62704",
    ]


def test_a_leaf_exactly_at_the_chrome_ratio_stops_both_runs() -> None:
    """The same boundary the block sweep uses, applied to a single leaf:
    0.15 is the highest content share a line can have and still count as
    chrome, so a leaf sitting exactly on it is where each run stops. One
    step the other way and both runs would walk straight through it into
    the article behind."""
    chrome = [
        "Manage your preferences",
        "Copyright 2026 Acme Inc",
        "You received this because you signed up",
        "Update your email preferences",
        "Sent to you by Acme",
        "Add us to your address book",
        "Acme Inc, 12 Main Street, Springfield",
        "Was this forwarded to you?",
        "You can update your details here",
    ]
    content = "Rates held steady, and the chair said little."
    # 45 characters of content against 255 of chrome: 0.15 on the nose.
    assert len(content) / (len(content) + sum(len(line) for line in chrome)) == 0.15
    boundary = "<p>" + "<br>".join([content, *chrome]) + "</p>"
    html = (
        "<html><body>"
        f"<div>{_RUN_LEAD}{boundary}{_RUN_ARTICLE}</div>"
        f"<div>{_RUN_ARTICLE_2}{boundary}{_RUN_TAIL}</div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.html.count("Rates held steady") == 2, "neither run passes it"
    assert "Sign up here" not in cleaned.html, "each still takes the run's start"
    assert "Springfield, IL" not in cleaned.html


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


def test_a_duplicate_title_broken_up_by_inline_tags_is_still_matched() -> None:
    """Substack sets part of a headline in italics and part of a dateline
    in its own span, so neither line is one text node. Read back without a
    separator between the fragments they become "NeoNazis and the
    ImpotenceofTrumponomics" and "Sep7", and neither the title comparison
    nor the date test recognizes what it is looking at.

    The date sits directly under the title here, which is the ordinary
    shape - title, then dateline - and the one a lookahead starting a leaf
    too late would step straight over.
    """
    title = "Neo-Nazis and the Impotence of Trumponomics"
    html = (
        "<html><body><div>"
        "<h2>\n  Neo-Nazis and the <em>Impotence</em> of Trumponomics\n</h2>"
        "<p>\n  <span>Sep</span> 7\n</p>"
        "<p>Crude economics doesn't explain what just happened, and here is "
        "a real paragraph of genuine article prose about the subject.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html, title=title))
    assert "Neo-Nazis" not in cleaned.html
    assert "Crude economics doesn't explain" in cleaned.html
    # The report names the lines as they read, not as the template wrapped
    # them: this removal is the one the reader is told is not data loss,
    # so it has to be legible.
    assert [(b.text, b.kind) for b in cleaned.blocks_dropped] == [
        (title, "duplicate_title"),
        ("Sep 7", "duplicate_title"),
    ]


def test_a_masthead_above_a_duplicate_title_does_not_hide_it() -> None:
    """The duplicated header is rarely the very first thing in the body:
    a publication name and a dateline usually sit above it. Neither is the
    title, and neither is a reason to stop looking - and the dateline
    above must not be mistaken for the one that closes the block, which
    would leave the block bounded backwards and nothing removed."""
    title = "Neo-Nazis and the Impotence of Trumponomics"
    html = (
        "<html><body><div>"
        "<p>THE ARGUMENT</p>"
        "<p>Sep 7</p>"
        f"<h2>{title}</h2>"
        "<p>Paul Krugman</p>"
        "<p>Sep 7</p>"
        "<p>Crude economics doesn't explain what just happened, and here is "
        "a real paragraph of genuine article prose about the subject.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html, title=title))
    assert "Neo-Nazis" not in cleaned.html
    assert "Paul Krugman" not in cleaned.html
    assert "Crude economics doesn't explain" in cleaned.html


def test_a_line_with_no_words_in_it_is_not_a_match_for_any_title() -> None:
    """A section break set as "* * *" normalizes to nothing at all, and
    nothing is a prefix of every title there has ever been. Without the
    guard the very first such line in the leading region would be taken
    for the duplicated headline, and everything down to the next dateline
    would go with it."""
    html = (
        "<html><body><div>"
        "<p>* * *</p>"
        "<p>Sep 7</p>"
        "<p>Crude economics doesn't explain what just happened, and here "
        "is a real paragraph of genuine article prose about the subject.</p>"
        "<p>A second paragraph carrying the argument along towards its end "
        "in the usual way.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(
        document(html, title="Neo-Nazis and the Impotence of Trumponomics")
    )
    assert "* * *" in cleaned.html
    assert "Sep 7" in cleaned.html


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
    # The Subject punctuates it differently from the body, which is the
    # other half of what normalizing is for: the dash is only in one copy,
    # so the two agree on their words and on nothing else.
    cleaned = clean_document(
        document(html, title="What's new in DevEx September 2 edition")
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
# only removes characters Python's str.isspace() recognizes, which misses
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
def test_every_image_decision_is_reported_and_counted() -> None:
    """The three outcomes in one document: a chart kept whole, a spacer
    dropped outright, and a data figure kept as a line of text. Each is
    counted and named, because the report is what tells the reader an
    image went and why - a spacer with no src at all still has to appear,
    rather than being recorded under whatever the parser returns for an
    attribute that is not there.

    Keeping an image is also not a reason to stop looking at the rest.
    """
    lead_in = (
        "<p>Real prose sets up the argument, and in the best model scores "
        "between the two countries:</p>"
    )
    html = (
        "<html><body><div>"
        f"{lead_in}"
        '<img src="https://example.com/first.png" width="600">'
        "<p>More prose after, long enough to read as an article paragraph "
        "and not a caption of any kind at all, and it ends like this:</p>"
        '<img src="https://example.com/second.png" width="600">'
        "<p>A third paragraph of ordinary prose, carrying the argument on "
        "towards whatever it is the writer wants to say next.</p>"
        '<img width="1">'
        '<img src="https://example.com/fig.png" width="600" '
        'alt="Fed funds rate against core inflation, 2019 to 2027">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 2
    assert [(d.src, d.reason) for d in cleaned.images_dropped] == [
        ("", "no lead-in or caption (decoration)"),
        ("https://example.com/fig.png", "kept as a text placeholder (figure)"),
    ]
    assert (
        '<p class="figure-placeholder">[figure: Fed funds rate against core '
        "inflation, 2019 to 2027]</p>" in cleaned.html
    )


def test_a_generic_alt_is_still_generic_with_a_full_stop_after_it() -> None:
    """ "Chart" names the kind of thing an image is, not what it shows, so
    it earns no placeholder. Writing it "Chart." changes nothing about
    that - and every one of these words is also a data term, so the
    trailing stop is all that stands between the two judgements."""
    html = (
        "<html><body><div>"
        "<p>Real prose sets up the argument, and here is the evidence for "
        "it, which we have been building towards all along.</p>"
        '<img src="https://example.com/chart.png" width="600" alt="Chart.">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "[figure:" not in cleaned.html


def test_an_alt_text_at_the_length_cap_is_kept_whole() -> None:
    """70 is the longest an alt text may be, not the first length that is
    too long. One character more and it is cut to 69 and given an
    ellipsis - and cut back through any space it lands on, so the ellipsis
    follows a word rather than floating clear of one."""
    exactly_70 = (
        "Fed funds rate against core inflation and unemployment, 2019 to 2027!!"
    )
    assert len(exactly_70) == 70
    cut_on_a_space = (
        "Fed funds rate against core inflation and unemployment, 2019 to 2027"
        " and beyond"
    )
    assert cut_on_a_space[68] == " "

    def placeholder_for(alt: str) -> str:
        html = (
            "<html><body><div>"
            "<p>Real prose sets up the argument, and here is the evidence "
            "for it, which we have been building towards all along.</p>"
            f'<img src="https://example.com/c.png" width="600" alt="{alt}">'
            "</div></body></html>"
        )
        return clean_document(document(html)).html

    cut_mid_word = (
        "Fed funds rate against core inflation and joblessness, 2019 to 2027 and beyond"
    )
    assert cut_mid_word[68] == "a", "this one keeps the character rstrip spares"

    assert f"[figure: {exactly_70}]" in placeholder_for(exactly_70)
    assert (
        "[figure: Fed funds rate against core inflation and joblessness, "
        "2019 to 2027 a\u2026]" in placeholder_for(cut_mid_word)
    )
    assert (
        "[figure: Fed funds rate against core inflation and unemployment, "
        "2019 to 2027\u2026]" in placeholder_for(cut_on_a_space)
    )


def test_an_image_exactly_at_the_minimum_width_is_wide_enough() -> None:
    """300px is the narrowest a figure may be, not the first width that is
    too narrow - a chart mailed at exactly the minimum is a chart."""
    html = (
        "<html><body><div><p>Real prose sets up the argument, and in the "
        "best model scores between the two countries:</p>"
        '<img src="https://example.com/chart.png" width="300">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 1


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
        "<tr><td><p>Media is shifting fast. <em>Our reporters</em> see it "
        "first.</p></td></tr>"
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
    # An ad is removed silently otherwise, and this pass takes whole blocks
    # of prose rather than a named phrase - so the report of what it took
    # is the only way a wrong removal becomes visible. It is printed for
    # the reader, one line per block, in the order they were taken.
    assert [block.text for block in cleaned.blocks_dropped] == [
        "A MESSAGE FROM AXIOS",
        "Media is shifting fast. Our reporters see it first.",
        (
            "Sara Fischer and Kerry Flynn go deeper than the headlines, "
            "tracking the deals and disruptions that matter."
        ),
    ]


def test_a_numbered_section_heading_stops_the_sponsor_removal() -> None:
    """Axios numbers its sections, and in every fixture the ad ends right
    before one. That is the hard stop that keeps this pass from running
    out of an ad and into the article behind it."""
    cleaned = clean_document(document(_sponsor_html("2. Americans' job market views")))
    assert "Americans" in cleaned.html


def test_a_section_number_set_in_bold_still_stops_the_removal() -> None:
    """Axios sets the number in its own tag, so the heading's text has to
    be read back with a separator between the fragments: run together,
    "2." and the title read as "2.Americans", and the pattern - a number,
    a period, then a space - no longer matches.

    The ad here is one block long, not two, so the heading is the second
    thing the loop looks at and the block cap is not what stops it.
    """
    html = (
        "<html><body><table>"
        "<tr><td><p>A MESSAGE FROM AXIOS</p></td></tr>"
        "<tr><td><p>Media is shifting fast. Our reporters see it first.</p></td></tr>"
        "<tr><td><p><strong>2.</strong> Americans' job market views</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Media is shifting fast" not in cleaned.html
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


def test_a_spacer_row_inside_an_ad_does_not_count_against_the_block_cap() -> None:
    """The cap counts blocks of ad copy, not table rows. A spacer row is
    swept up with the rest but spends none of the two, or a template that
    puts one in the middle of its ad would leave the second half of the ad
    standing."""
    html = (
        "<html><body><table>"
        "<tr><td><p>A MESSAGE FROM ACME</p></td></tr>"
        "<tr><td>\n\t\t</td></tr>"
        "<tr><td><p>Acme makes the finest anvils in the west.</p></td></tr>"
        "<tr><td><p>Buy one today and save a bundle.</p></td></tr>"
        "<tr><td><p>Meanwhile the Fed said nothing at all this month.</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "finest anvils" not in cleaned.html
    assert "Buy one today" not in cleaned.html
    assert "Fed said nothing" in cleaned.html


def test_an_ad_body_exactly_at_the_character_cap_is_still_taken() -> None:
    """600 is the longest an ad body may be, not the first length that is
    too long: the measured ads run to 394 and the article blocks behind
    them start at 831, so the boundary itself belongs to the ad side."""
    head = "Acme anvils, forged in the west. " * 18
    tail = "Buy it"
    # The line renders as head, one separator space, then tail - 600 on
    # the nose, the longest _SPONSOR_MAX_CHARS allows.
    assert len(head.strip()) + 1 + len(tail) == 600
    html = (
        "<html><body><table>"
        "<tr><td><p>A MESSAGE FROM ACME</p></td></tr>"
        f"<tr><td><p>{head}<em>{tail}</em></p></td></tr>"
        "<tr><td><p>2. The Fed said nothing at all this month.</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "forged in the west" not in cleaned.html
    assert "Fed said nothing" in cleaned.html


def test_the_anchor_climb_stops_at_the_body() -> None:
    """Bulk mail often trails junk after </body>, which the parser leaves
    as a sibling of body rather than tidying away. Without the stop, a
    header with no ad under it would climb all the way out, anchor on the
    body itself, and take the entire newsletter as its ad copy."""
    html = (
        "<html><body>"
        "<div><p>The Fed declined to move rates this month, which surprised "
        "almost nobody watching the minutes.</p></div>"
        "<div><p>A MESSAGE FROM OUR SPONSOR</p></div>"
        "</body><div>Sent by Acme, 12 Main St.</div></html>"
    )
    cleaned = clean_document(document(html))
    assert "surprised almost nobody" in cleaned.html


def test_a_header_that_cannot_be_anchored_does_not_end_the_search() -> None:
    """Sitting loose in the body as a bare text node, a header has no
    ancestor at all between it and the stop, so there is nothing to anchor
    an ad on. That is a reason to move to the next header, not to stop
    looking at them - the real block is further down."""
    html = (
        "<html><body>"
        "<div><p>The Fed declined to move rates this month, which surprised "
        "almost nobody watching the minutes.</p></div>"
        "A MESSAGE FROM NOWHERE"
        "<table>"
        "<tr><td><p>A MESSAGE FROM ACME</p></td></tr>"
        "<tr><td><p>Acme makes the finest anvils in the west.</p></td></tr>"
        "<tr><td><p>2. The Fed said nothing at all this month.</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "finest anvils" not in cleaned.html
    assert "Fed said nothing" in cleaned.html


def test_a_header_that_is_one_line_among_paragraphs_takes_no_neighbors() -> None:
    """ "A message from" also opens ordinary sentences. When the header
    shares its container with nothing - the test is that the block it sits
    in is the very thing the climb anchored on - there is no ad under it,
    and taking the two paragraphs that follow would eat the article. The
    inline wrapper matters: it is the enclosing block that has to be
    compared against the anchor, not the <strong> around the words."""
    html = (
        "<html><body>"
        "<div>"
        "<p>The Fed declined to move rates this month, which surprised "
        "almost nobody watching the minutes.</p>"
        "<p><strong>A message from our friends at the desk</strong></p>"
        "<p>The chair took questions for an hour and gave nothing away, "
        "which is how these things usually go.</p>"
        "</div>"
        "<table>"
        "<tr><td><p>A MESSAGE FROM ACME</p></td></tr>"
        "<tr><td><p>Acme makes the finest anvils in the west.</p></td></tr>"
        "<tr><td><p>2. Warsh's labor market calculus.</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "took questions for an hour" in cleaned.html
    assert "finest anvils" not in cleaned.html, "the real ad still goes"


def test_a_detached_header_does_not_end_the_search() -> None:
    """One sponsor block can carry a later header away with it. Reaching
    that header's detached node is a reason to step over it, not to stop
    looking - there may be a third block behind it, as there is here."""
    html = (
        "<html><body><table>"
        "<tr><td><p>A MESSAGE FROM ONE</p></td></tr>"
        "<tr><td><p>Acme makes the finest anvils in the west.</p></td></tr>"
        "<tr><td><p>A MESSAGE FROM TWO</p></td></tr>"
        "<tr><td><p>A MESSAGE FROM THREE</p></td></tr>"
        "<tr><td><p>Acme also makes the loudest whistles going.</p></td></tr>"
        "<tr><td><p>2. The Fed said nothing at all this month.</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "finest anvils" not in cleaned.html
    assert "loudest whistles" not in cleaned.html
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


# ---------------------------------------------------------------------------
# The sign-off guard. Once the boilerplate behind it is chrome, the author's
# valediction is the next thing the backward walk reaches - and it scores as
# chrome too ("Have a great weekend," is short and ends in a comma). Losing
# it reads as the newsletter being cut off mid-thought.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signoff",
    [
        "Have a great weekend,\nJon",
        "Until next time,",
        "Thanks for reading! We’ll see you tomorrow.",
        "See you next week,\nSam",
        "Best,\nReuven",
    ],
)
def test_a_sign_off_stops_the_trailing_chrome_run(signoff: str) -> None:
    from newsprint.clean import _is_signoff

    assert _is_signoff(signoff)


@pytest.mark.parametrize(
    "not_a_signoff",
    [
        # Axios's, which leads into a referral ask and must stay chrome.
        "Thanks for reading! Please invite your friends to join AM.",
        # A paragraph that merely opens with the words keeps going.
        (
            "Have a great deal of sympathy for the central bankers here:\n"
            "they are being asked to do something no one has managed\n"
            "before, and the politics are worse than the economics."
        ),
        "Best of all, the data is public.",
    ],
)
def test_prose_is_not_mistaken_for_a_sign_off(not_a_signoff: str) -> None:
    from newsprint.clean import _is_signoff

    assert not _is_signoff(not_a_signoff)


def test_the_authors_sign_off_survives_the_boilerplate_behind_it() -> None:
    """End to end: the FAQ block goes, the valediction stays."""
    html = (
        "<html><body><div>"
        "<p>The Fed declined to move rates this month, which surprised almost "
        "nobody who had been watching the minutes and the dot plot closely.</p>"
        "<p>Have a great weekend,<br>Jon</p>"
        "<p>Need help? Review our FAQ page or contact us for assistance. "
        "For brand partnerships, email ads@puck.news.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Have a great weekend" in cleaned.html
    assert "brand partnerships" not in cleaned.html
    assert "surprised almost nobody" in cleaned.html


# ---------------------------------------------------------------------------
# The paths that only the author's private archive used to reach. Covered
# here as units so a contributor - who cannot have that archive, since it is
# other people's copyrighted mail - still gets the full guarantee.
# ---------------------------------------------------------------------------


def test_an_html_comment_is_not_the_nearest_text_before_a_figure() -> None:
    """Outlook's MSO conditional markup is a string node but renders as
    nothing, so a lead-in search must look straight through it."""
    from bs4 import BeautifulSoup

    from newsprint.clean import _nearest_rendered_text

    soup = BeautifulSoup(
        "<div><p>Here is the chart:</p><!--[if mso]>hidden<![endif]-->"
        '<img src="https://example.com/c.png" width="600"></div>',
        "lxml",
    )
    image = soup.find("img")
    assert _nearest_rendered_text(image, forward=False) == "Here is the chart:"


def test_alt_text_with_no_data_word_gets_no_placeholder() -> None:
    """A wide image whose alt describes a promo rather than data - which
    is what 250 of the 292 candidates in the archive turned out to be."""
    html = (
        "<html><body><div><p>Real prose that continues for a good while "
        "here, long enough to read as an article.</p>"
        '<img src="https://example.com/promo.png" width="600" '
        'alt="The Conflict Playbook on MasterClass"></div></body></html>'
    )
    cleaned = clean_document(document(html))
    assert "[figure:" not in cleaned.html
    assert "<img" not in cleaned.html


def test_a_trailing_spacer_row_does_not_end_the_anchor_climb() -> None:
    """Puck nests the ad's own header in an inner table, and closes that
    table with a spacer row - so the header's row has a sibling, but not
    one that says anything. Stopping there would anchor the block on the
    inner table and leave the ad copy, which sits a level further out,
    standing."""
    html = (
        "<html><body><table>"
        "<tr><td><table>"
        "<tr><td><p>A MESSAGE FROM ACME</p></td></tr>"
        "<tr><td>\n\t\t</td></tr>"
        "</table></td></tr>"
        "<tr><td><p>Acme makes the finest anvils in the west.</p></td></tr>"
        "<tr><td><p>2. The Fed said nothing at all this month.</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "A MESSAGE FROM" not in cleaned.html
    assert "finest anvils" not in cleaned.html
    assert "Fed said nothing" in cleaned.html


def test_a_sponsor_anchor_looks_past_empty_siblings() -> None:
    """Bulk-mail HTML is full of spacer rows with no text in them; the
    anchor is the first ancestor with a sibling that actually says
    something."""
    html = (
        "<html><body><table>"
        "<tr><td><p>A MESSAGE FROM ACME</p></td></tr>"
        # Indented rather than truly empty: the spacer rows bulk mail
        # emits carry the template's own newlines and tabs.
        "<tr><td>\n\t\t</td></tr>"
        "<tr><td><p>Acme makes the finest anvils in the west.</p></td></tr>"
        # Longer than _SPONSOR_MAX_CHARS, which is what stops the block
        # from running out of the ad and into the article.
        "<tr><td><p>"
        + ("The Fed declined to move rates this month. " * 18)
        + "surprised almost nobody at all.</p></td></tr>"
        "</table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "finest anvils" not in cleaned.html
    assert "surprised almost nobody" in cleaned.html


# ---------------------------------------------------------------------------
# Finding the content root, and pruning what has nothing to say. Both walk
# a tree whose whitespace is exactly as the mail template left it, which is
# what these fixtures keep: newlines and indentation between every tag.
# ---------------------------------------------------------------------------

_INDENTED = (
    "<html>\n"
    "  <body>\n"
    "    <div>\n"
    "      <p>The Fed declined to move rates this month, which surprised "
    "almost nobody who had been watching the minutes closely.</p>\n"
    "      <p>Markets took the news calmly, which is not at all what "
    "anyone had forecast at the start of a week like this one.</p>\n"
    "    </div>\n"
    "  </body>\n"
    "</html>"
)


def test_the_cleaned_document_carries_no_page_scaffolding() -> None:
    """What comes back is the newsletter's content, ready to drop into a
    cell - not a page. The <html> and <body> the parser synthesizes around
    every input are where the search for that content starts, never part
    of what it returns."""
    cleaned = clean_document(document(_INDENTED))
    assert "<html" not in cleaned.html
    assert "<body" not in cleaned.html
    assert cleaned.html.startswith("<p>The Fed declined")


def test_indentation_between_tags_is_not_a_second_child() -> None:
    """The descent through layout wrappers counts children that say
    something. The newlines and tabs a template leaves between its tags
    say nothing, and counting them would stop the descent at the first
    wrapper - leaving the wrapper itself as the root, and its single div
    as the one block every later pass gets to judge as a unit."""
    cleaned = clean_document(document(_INDENTED))
    assert "<div" not in cleaned.html, "the wrapper is descended through"
    assert "surprised almost nobody" in cleaned.html
    assert "took the news calmly" in cleaned.html


def test_an_empty_wrapper_beside_the_real_one_is_not_a_second_child() -> None:
    """Same descent, the other kind of silent child: a wrapper the
    template emitted and never filled. It is an element, so it survives
    the isinstance test, and only having nothing to say keeps it from
    counting - which is what lets the descent go on into the wrapper that
    does have something."""
    prose = (
        "<p>The Fed declined to move rates this month, which surprised "
        "almost nobody who had been watching the minutes closely.</p>"
        "<p>Markets took the news calmly, which is not at all what anyone "
        "had forecast at the start of a week like this one.</p>"
    )
    html = f"<html><body><div>{prose}</div><div>\n   </div></body></html>"
    cleaned = clean_document(document(html))
    assert "<div" not in cleaned.html
    assert cleaned.html.startswith("<p>The Fed declined")


def test_a_title_with_an_empty_wrapper_after_it_is_still_a_leaf() -> None:
    """A headline closed by an empty div. It is an element and it is not
    inline, so only having nothing to say keeps it from counting as a
    block child - and if it counted, the walk would recurse past the
    headline into it, the headline's own text would never be offered to
    any pass, and the duplicated header would stay on the page."""
    title = "Neo-Nazis and the Impotence of Trumponomics"
    html = (
        "<html><body><div>"
        f"<div>{title}<div>  </div></div>"
        "<p>Sep 7</p>"
        "<p>Crude economics doesn't explain what just happened, and here "
        "is a real paragraph of genuine article prose.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html, title=title))
    assert "Neo-Nazis" not in cleaned.html
    assert "Crude economics" in cleaned.html


def test_an_invisible_paragraph_mid_document_is_pruned() -> None:
    """Zero-width spaces are how bulk mail pads its layout. An element
    holding nothing else has no text to mis-score and simply goes -
    including one sitting between two paragraphs, which is a reason to
    keep sweeping rather than to stop."""
    html = (
        "<html><body><div>"
        "<p>The Fed declined to move rates this month, which surprised "
        "almost nobody<br>who had been watching the minutes closely.</p>"
        "<p>\u200b\u200b</p>"
        "<p>Markets took the news calmly, which is not at all what anyone "
        "had forecast at the start of a week like this one.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "\u200b" not in cleaned.html
    # The <br> above it is exempt, and stepping over it is not a reason to
    # stop sweeping.
    assert "<br" in cleaned.html
    assert "took the news calmly" in cleaned.html


def test_a_bare_text_node_of_chrome_is_extracted() -> None:
    """A postal address is often just text between two <br> tags, never
    its own element - so it is extracted rather than decomposed."""
    html = (
        "<html><body><div>"
        "<p>Real article prose with enough substance to read as genuine "
        "content rather than a label of some kind.</p>"
        "<p>Some Company<br>PMB 12, Springfield, IL 62704<br>© 2026</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Springfield" not in cleaned.html
    assert "Real article prose" in cleaned.html


def test_the_caption_search_walks_past_inline_tags_and_empty_blocks() -> None:
    """Between an image and its caption sit inline wrappers with no name
    this cares about, and blocks with no text in them at all. Neither is
    the caption."""
    from bs4 import BeautifulSoup

    from newsprint.clean import _nearest_block_text

    soup = BeautifulSoup(
        '<div><img src="https://example.com/s.png" width="600">'
        "<span>inline, not a block</span>"
        "<p></p>"
        "<p>(<a href='https://example.com/post'>Link</a>)</p></div>",
        "lxml",
    )
    assert _nearest_block_text(soup.find("img")) == "( Link )"


# ---------------------------------------------------------------------------
# _is_standalone_line's <br>-run boundaries. This is the guard that stops
# _strip_line_chrome deleting "unsubscribe" quoted inside a real sentence,
# so its failure mode is losing content - and mutation testing found the
# boundary arithmetic almost entirely untested: 16 survivors in this one
# function.
# ---------------------------------------------------------------------------


def _standalone(html: str, text: str) -> bool:
    """Is the node whose text is `text` alone on its own rendered line?"""
    from bs4 import BeautifulSoup

    from newsprint.clean import _is_standalone_line

    soup = BeautifulSoup(html, "lxml")
    node = soup.find(string=lambda s: s and s.strip() == text)
    assert node is not None, f"{text!r} not found in the fixture"
    return _is_standalone_line(node)


def test_a_line_of_its_own_between_two_breaks_is_standalone() -> None:
    assert _standalone("<p>Before<br>Unsubscribe<br>After</p>", "Unsubscribe")


def test_the_first_line_of_a_block_is_standalone() -> None:
    """start walks back to index 0 and stops there; walking past it would
    pull the previous line into this one's run."""
    assert _standalone("<p>Unsubscribe<br>After</p>", "Unsubscribe")


def test_the_last_line_of_a_block_is_standalone() -> None:
    """end stops at the final sibling; running past it would index off the
    end of the list."""
    assert _standalone("<p>Before<br>Unsubscribe</p>", "Unsubscribe")


def test_text_sharing_a_line_with_prose_before_it_is_not_standalone() -> None:
    """The adversarial case this guard exists for: a chrome-shaped word
    sitting in a real sentence."""
    assert not _standalone(
        "<p>There is an <span>Unsubscribe</span> link below.</p>", "Unsubscribe"
    )


def test_text_sharing_a_line_with_prose_after_it_is_not_standalone() -> None:
    assert not _standalone(
        "<p><span>Unsubscribe</span> from this if you would rather not.</p>",
        "Unsubscribe",
    )


def test_prose_on_the_far_side_of_a_break_does_not_count() -> None:
    """Only the node's own <br>-delimited run matters; a sentence one line
    away is a different line."""
    assert _standalone(
        "<p>A real sentence with words in it.<br>Unsubscribe<br>"
        "Another real sentence entirely.</p>",
        "Unsubscribe",
    )


def test_an_inline_wrapper_is_followed_up_a_level() -> None:
    """<a><span>Unsubscribe</span></a> is still alone on its line, and the
    check has to ascend through both wrappers to find that out."""
    assert _standalone(
        "<p>Before<br><a><span>Unsubscribe</span></a><br>After</p>", "Unsubscribe"
    )


def test_an_inline_wrapper_beside_prose_is_not_standalone() -> None:
    """Ascending must keep checking: the <a> is alone among its own
    siblings, but its parent shares a line with a sentence."""
    assert not _standalone(
        "<p>Please <a><span>Unsubscribe</span></a> if you would rather not.</p>",
        "Unsubscribe",
    )


def test_the_run_extends_one_sibling_at_a_time() -> None:
    """Empty inline elements sit on both sides of the target within its
    own <br>-delimited run. Stepping the run's end by two skips over the
    empty span and lands on the <br>, taking a sibling from the *next*
    line into this one's run."""
    assert _standalone(
        "<p><span></span>Unsubscribe<span></span><br>tail</p>", "Unsubscribe"
    )


# ---------------------------------------------------------------------------
# _strip_line_chrome's sweep. To reach this pass and only this pass, the
# chrome has to sit on its own line *inside* a block whose other lines are
# real prose: the block-level pass scores that block as content and leaves
# it whole, so anything these fixtures lose, the line sweep took.
# ---------------------------------------------------------------------------

_PROSE_A = (
    "The Federal Reserve declined to move rates this month, which "
    "surprised almost nobody who had been watching the minutes closely."
)
_PROSE_B = (
    "Markets took the news calmly, which is not at all what anyone had "
    "forecast at the start of a week like this one."
)


def _around(middle: str) -> str:
    """A paragraph with real prose on both sides of middle."""
    return f"<html><body><div><p>{_PROSE_A}<br>{middle}<br>{_PROSE_B}</p></div></body></html>"


def test_a_bare_chrome_text_node_between_prose_lines_is_removed() -> None:
    """A chrome line is often a bare string between two <br> tags, with no
    element of its own - invisible to any walk over tags alone."""
    cleaned = clean_document(document(_around("\n  Unsubscribe\n  ")))
    assert "Unsubscribe" not in cleaned.html
    assert "surprised almost nobody" in cleaned.html
    assert "took the news calmly" in cleaned.html
    # Every removal is reported, and that report is printed for the reader
    # to check: it records the line, not the newlines and indentation the
    # mail template happened to wrap it in.
    assert "Unsubscribe" in [block.text for block in cleaned.blocks_dropped]


def test_inline_chrome_alone_on_its_line_is_removed() -> None:
    """The standalone check is what separates an inline node that owns its
    line from one sharing it with prose. Skipping every inline node instead
    would leave the <span>- and <a>-wrapped chrome most newsletters use.

    Splitting the phrase across two spans is how mail templates that style
    part of a link actually look, and it means no single text node reads as
    chrome: only the enclosing <a>, joined back together, does."""
    middle = "<a href='#'>\n  <span>View</span> <span>in browser</span>\n</a>"
    cleaned = clean_document(document(_around(middle)))
    assert "in browser" not in cleaned.html
    assert "took the news calmly" in cleaned.html
    assert "View in browser" in [block.text for block in cleaned.blocks_dropped]


def test_a_chrome_word_that_opens_or_closes_a_shared_line_is_kept() -> None:
    """The run a node shares its line with reaches out in both directions
    to the nearest break, and the node's own position in it is not
    special. A chrome word at the very start of that run has as much
    beside it as one in the middle, and so does one at the very end."""

    def cleaned_html(inner: str) -> str:
        html = (
            f"<html><body><div><p>{inner}</p></div>"
            "<p>A second block of real prose, long enough to stand as an "
            "article paragraph.</p></body></html>"
        )
        return clean_document(document(html)).html

    ends_the_run = f"{_PROSE_A}<br><em>Look for the </em>Unsubscribe<br>{_PROSE_B}"
    opens_the_run = f"Unsubscribe<em> link</em><br>{_PROSE_A}<br><em>{_PROSE_B}</em>"
    # And one where the run starts at the paragraph's own first child, so
    # the walk back has to reach index 0 rather than stop just above it.
    second_in_the_paragraph = f"<em>Look for the </em>Unsubscribe<br>{_PROSE_A}"
    assert "Unsubscribe" in cleaned_html(ends_the_run)
    assert "Unsubscribe" in cleaned_html(opens_the_run)
    assert "Unsubscribe" in cleaned_html(second_in_the_paragraph)


def test_the_scaffolding_around_a_removed_chrome_line_goes_with_it() -> None:
    """Bulk mail wraps every footer link in its own nest of containers,
    and taking the links out leaves the containers standing. They are not
    empty, either: the template's own spaces are still in them, and a
    space counts as visible text - deliberately, so that a real one is
    never pruned - so the sweep at the end of the run leaves these behind
    and they print as a blank gap.

    The innermost wrapper here holds two chrome lines rather than one,
    which is what it takes to reach this walk at all: a wrapper holding a
    single chrome line reads as that chrome line itself and is removed
    whole, ancestors and all. With two, the wrapper's own text is neither
    line, so only the lines match and the wrapper is left empty rather
    than matched.

    The walk has to keep going, too, rather than stop one level up - each
    wrapper in the nest has the template's spaces in it, so each in turn
    is left looking visible to that end-of-run sweep.
    """
    scaffolding = (
        "<div> <div> <div> <span>Unsubscribe</span><br>"
        "<span>View in browser</span> </div> </div> </div>"
    )
    html = (
        "<html><body>"
        f"<div><p>{_PROSE_A}</p>{scaffolding}<p>{_PROSE_B}</p></div>"
        "<div><p>A second top-level block of prose, long enough to stand "
        "as an article paragraph on its own.</p></div>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Unsubscribe" not in cleaned.html
    assert "View in browser" not in cleaned.html
    # The two that are left are the block this sat in and the one after it.
    assert cleaned.html.count("<div") == 2
    assert "surprised almost nobody" in cleaned.html
    assert "took the news calmly" in cleaned.html


def test_a_chrome_line_between_two_paragraphs_owns_its_line() -> None:
    """A bare chrome line sitting between two block siblings rather than
    between two <br> tags. The blocks bound its line without being part of
    it - which is the whole point of walking out to a boundary and
    stopping there rather than stepping over it."""
    prose = (
        f"<div><p>{_PROSE_A}</p>Unsubscribe<p>{_PROSE_B}</p></div>"
        "<p>A second block of real prose, long enough to stand as an "
        "article paragraph.</p>"
    )
    cleaned = clean_document(document(f"<html><body>{prose}</body></html>"))
    assert "Unsubscribe" not in cleaned.html
    assert "surprised almost nobody" in cleaned.html
    assert "took the news calmly" in cleaned.html


def test_an_empty_span_beside_a_chrome_line_does_not_save_it() -> None:
    """Templates scatter empty spans everywhere. One sharing a chrome
    line's run is not something else on that line, so the line is still
    the whole of what is there."""
    html = (
        "<html><body><div>"
        f"<p>{_PROSE_A}<br><span>  </span>Unsubscribe<br>{_PROSE_B}</p>"
        "</div>"
        "<p>A second block of real prose, long enough to stand as an "
        "article paragraph.</p>"
        "</body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Unsubscribe" not in cleaned.html
    assert "took the news calmly" in cleaned.html


def test_a_chrome_word_inside_a_sentence_survives_the_sweep() -> None:
    """The other half of that guard: an inline node sharing its line with
    prose is left alone, however chrome-shaped its own text."""
    middle = "Look for the <a href='#'>Unsubscribe</a> link at the bottom of this page."
    cleaned = clean_document(document(_around(middle)))
    assert "Unsubscribe" in cleaned.html


def test_chrome_late_in_the_sweep_is_still_removed() -> None:
    """The sweep visits every candidate, and it skips a great many on the
    way: whitespace between tags, prose that shares its line with a link,
    and the text nodes inside a chrome element an earlier match already
    decomposed. Abandoning the loop at any of those - rather than stepping
    over it - would leave every candidate after it in place, so this puts
    one of each ahead of a plain chrome line and checks the line still
    goes."""
    middle = (
        "Look for the <a href='#'>tiny link</a> at the bottom of this page."
        "<br>\n  <a href='#'><span>View</span> <span>in browser</span></a>\n  "
        "<br>Unsubscribe"
    )
    cleaned = clean_document(document(_around(middle)))
    assert "in browser" not in cleaned.html
    assert "Unsubscribe" not in cleaned.html
    assert "tiny link" in cleaned.html
    assert "took the news calmly" in cleaned.html


def test_an_uncaptioned_chart_between_two_paragraphs_is_kept() -> None:
    """Apricitas Economics sets a chart every few hundred words and never
    introduces one: no lead-in colon, no Source caption, alt="". The
    author-introduces-it rule can never fire for them, so every chart in a
    charts newsletter was dropped. Prose on both sides is the structural
    form of the same claim - this image interrupts an argument.
    """
    html = (
        "<html><body><div>"
        "<p>Demand from AI and heavy industry is driving load growth but "
        "also outstripping growth in power infrastructure.</p>"
        '<img src="https://example.com/chart.png" alt="" '
        'width="550" height="351.3">'
        "<p>The return of sustained load growth after more than a decade "
        "and a half of stagnation is the first fundamental change.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 1
    assert cleaned.images_dropped == ()


def test_a_labelled_banner_between_two_paragraphs_is_not_kept() -> None:
    """The same shape, but the image names itself. A masthead, a section
    header or a sponsor's logo carries alt or title text, because the
    sender wants it to read as that brand when images are blocked. An
    author's chart is set for looking at, not for naming."""
    html = (
        "<html><body><div>"
        "<p>Demand from AI and heavy industry is driving load growth but "
        "also outstripping growth in power infrastructure.</p>"
        '<img src="https://example.com/logo.png" alt="Bayer" '
        'width="550" height="351.3">'
        "<p>The return of sustained load growth after more than a decade "
        "and a half of stagnation is the first fundamental change.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 0


def test_an_image_with_nothing_before_it_is_not_a_figure() -> None:
    """A masthead opens the newsletter: prose after it, nothing before.

    The backward walk has to skip the image's own ancestors. A containing
    <div> starts before the image but does not end before it, so its text
    is the image's whole surroundings - letting the prose *after* the
    image answer the question "is there prose before it?" and waving
    every masthead through.
    """
    html = (
        "<html><body><div>"
        '<img src="https://example.com/masthead.png" alt="" '
        'width="550" height="351.3">'
        "<p>The return of sustained load growth after more than a decade "
        "and a half of stagnation is the first fundamental change.</p>"
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 0


def test_an_image_with_nothing_after_it_is_not_a_figure() -> None:
    """The mirror case: a sign-off logo, with the article above it and
    nothing below."""
    html = (
        "<html><body><div>"
        "<p>The return of sustained load growth after more than a decade "
        "and a half of stagnation is the first fundamental change.</p>"
        '<img src="https://example.com/logo.png" alt="" '
        'width="550" height="351.3">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert cleaned.images_kept == 0


def _figure_between(before: str, after: str, **attrs: str) -> str:
    """A wide, unlabelled image with `before` above it and `after` below."""
    extra = "".join(f' {name}="{value}"' for name, value in attrs.items())
    return (
        f"<html><body><div><p>{before}</p>"
        f'<img src="https://example.com/chart.png" alt="" '
        f'width="550" height="351.3"{extra}>'
        f"<p>{after}</p></div></body></html>"
    )


_PROSE = (
    "The return of sustained load growth after more than a decade and a "
    "half of stagnation is the first fundamental change."
)


def test_a_title_alone_is_enough_to_name_an_image() -> None:
    """Puck's section headers carry both alt and title; some senders set
    only one. Either one is the sender naming the image, which is what
    disqualifies it."""
    html = _figure_between(_PROSE, _PROSE, title="The Daily Courant")
    assert clean_document(document(html)).images_kept == 0


def test_a_caption_length_line_on_one_side_is_not_prose() -> None:
    """A chart stacked directly on another chart has only its own caption
    between them. That is not the argument resuming, so neither image is
    an unintroduced figure - both want a real paragraph on each side."""
    html = _figure_between("Change in electricity sales", _PROSE)
    assert clean_document(document(html)).images_kept == 0


def test_exactly_the_minimum_context_on_both_sides_is_enough() -> None:
    """80 characters is the shortest paragraph that counts, not the
    longest that does not."""
    exactly_80 = "x" * 80
    assert len(exactly_80) == 80
    html = _figure_between(exactly_80, exactly_80)
    assert clean_document(document(html)).images_kept == 1


def test_a_height_with_two_decimal_points_is_not_a_number() -> None:
    """Only one decimal point is forgiven, because a height is written
    "351.3" and nothing else about an image is written "1.2.3". A second
    point means the attribute is not a measurement, and an image whose
    dimensions cannot be read is not one we claim to recognize."""
    html = (
        "<html><body><div>"
        f"<p>{_PROSE}</p>"
        '<img src="https://example.com/chart.png" alt="" '
        'width="550" height="1.2.3">'
        f"<p>{_PROSE}</p></div></body></html>"
    )
    assert clean_document(document(html)).images_kept == 0
