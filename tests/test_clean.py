from datetime import UTC, datetime
from pathlib import Path

import pytest

from shabbat_print.clean import clean_document
from shabbat_print.models import Document, Origin

FIXTURES = Path(__file__).parent / "fixtures"


def document(html: str) -> Document:
    return Document(
        origin=Origin(kind="email", identifier="<x@example.com>"),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=html,
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


def test_leaf_tags_are_kept_whole_not_fragmented() -> None:
    """A paragraph must not be torn apart looking for its worst line."""
    html = (
        "<html><body><div><p>The chapter closes on a long, thoughtful note "
        "about markets and memory, and if it moved you at all, there is an "
        '<a href="https://example.com/u">unsubscribe</a> link somewhere '
        "below, which almost nobody ever clicks.</p></div></body></html>"
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


def test_other_document_fields_are_preserved() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert cleaned.title == "An Issue"
    assert cleaned.publication == "Test Weekly"
    assert cleaned.origin.identifier == "<x@example.com>"


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
    dickerson = "johnfdickerson-substack-com.eml"
    mandarin = "realtimemandarin-lessons-substack-com.eml"
    assert dickerson in cleaned_by_name, "fixture corpus changed: re-pin this test"
    assert mandarin in cleaned_by_name, "fixture corpus changed: re-pin this test"
    assert "Listen now (25 mins)" in cleaned_by_name[dickerson].html
    assert (
        "Listen now (8 mins) | 与运动员首次合作效果惊人"
        in cleaned_by_name[mandarin].html
    )
